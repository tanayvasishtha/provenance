"""The agent: Claude decides which graph queries to run; every fact in its answer comes from a tool result.

Three modes, chosen per request:
- live: a model key is set, so Claude runs and each response is recorded to data/agent_replay/.
- replay: no key, but this exact conversation was recorded, so the recorded responses are served.
- graph-only: no key and no recording; a rule-based planner calls the same tools and fills a template.
"""

import hashlib
import json
import logging
import re

from . import memory, queries
from .baseline import moiety
from .config import ROOT, settings

log = logging.getLogger("agent")
MODEL = "claude-opus-5-5"
REPLAY = ROOT / "data" / "agent_replay"
MAX_STEPS = 8

SYSTEM = """You help a hospital pharmacist during drug shortages. You answer from a FalkorDB graph of FDA public records: \
drug labels and the plants named on them, the FDA establishment registry, the FDA drug shortage list, and FDA Import Alert 66-40.

How to work:
- Use the tools for every fact. Never name a plant, FEI number, date or count that a tool did not return.
- When asked about a substitute, check whether it shares a manufacturing plant with the drug in shortage, and say which plant.
- "Same class" means the same FDA pharmacologic class. It does not mean two drugs are interchangeable for a patient; say so when \
you suggest one, and leave the clinical choice to the pharmacist.
- Label counts are not market share. FDA does not publish how much each plant ships.
- When the pharmacist says they chose a substitute, record it with record_decision for their hospital.
- Answer in plain sentences, short, with the plant names and FEI numbers that matter. No headings, no emoji."""


def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


DRUG = {"drug": {"type": "string", "description": "Substance name as on FDA labels, e.g. CISPLATIN or MIDAZOLAM HYDROCHLORIDE."}}
HOSP = {"hospital": {"type": "string", "enum": list(memory.DEMO_HOSPITALS), "description": "Hospital tenant slug."}}
FEI = {"fei": {"type": "string", "description": "FDA Facility Establishment Identifier of the plant."}}

def _tool(name: str, description: str, props: dict, required: list[str]) -> dict:
    return {"name": name, "description": description, "input_schema": _schema(props, required), "strict": True}


DECISION = {**HOSP, **DRUG, "substitute": {"type": "string"}, "reason": {"type": "string"}}

TOOLS = [
    _tool("plants_for_drug", "Every plant that manufactures the drug, with FEI, country, label count and FDA import alert dates.",
          DRUG, ["drug"]),
    _tool("substitute_trap", "Same-class substitutes for the drug and the manufacturing plants each shares with it.",
          DRUG, ["drug"]),
    _tool("safe_substitutes", "Same-class substitutes that share no manufacturing plant with the drug, "
          "with label count, flagged plants and shortage status.", DRUG, ["drug"]),
    _tool("plant_impact", "Every drug a plant manufactures, its share of US labels, and whether it is on the shortage list now.",
          FEI, ["fei"]),
    _tool("riskiest_plants", "Plants ranked by how much of today's shortage drugs they carry.",
          {"limit": {"type": "integer", "description": "How many plants, 1 to 25."}}, ["limit"]),
    _tool("bridge_plants", "Plants that bridge the most separate drug supply chains, by betweenness centrality "
          "(FalkorDB algo.betweenness). A failure there spreads shortages across drug classes.",
          {"limit": {"type": "integer", "description": "How many plants, 1 to 25."}}, ["limit"]),
    _tool("hospital_memory", "What this hospital stocks and every substitution it decided before, newest first.",
          HOSP, ["hospital"]),
    _tool("record_decision", "Store the pharmacist's chosen substitute in the hospital's own graph.",
          DECISION, ["hospital", "drug", "substitute", "reason"]),
    _tool("plant_alert", "If this plant stops, which drugs and past decisions each hospital is exposed through.",
          FEI, ["fei"]),
]


def _other(rows: list[dict], drug: str) -> list[dict]:
    return [r for r in rows if moiety(r["substitute"]) != moiety(drug.upper())]


def run_tool(name: str, args: dict) -> tuple[dict | list, str]:
    """Execute one tool. Returns (result, the Cypher behind it) so the UI can show both."""
    if name == "plants_for_drug":
        return queries.drug_plants(args["drug"]), queries.DRUG_PLANTS
    if name == "substitute_trap":
        return _other(queries.trap(args["drug"]), args["drug"]), queries.TRAP
    if name == "safe_substitutes":
        return _other(queries.safe(args["drug"]), args["drug"]), queries.SAFE
    if name == "plant_impact":
        return queries.plant_down(args["fei"]), queries.PLANT_DOWN
    if name == "riskiest_plants":
        return queries.chokepoints(max(1, min(25, int(args["limit"])))), queries.CHOKEPOINTS
    if name == "bridge_plants":
        return queries.bridges(max(1, min(25, int(args["limit"])))), queries.BRIDGES
    if name == "hospital_memory":
        return memory.profile(args["hospital"]), "MATCH (:Hospital)-[:DECIDED]->(x:Decision) RETURN x  // in graph hospital:<slug>"
    if name == "record_decision":
        return memory.record_decision(args["hospital"], args["drug"], args["substitute"], args["reason"]), \
            "CREATE (h)-[:DECIDED]->(:Decision {...})  // in graph hospital:<slug>"
    if name == "plant_alert":
        return memory.plant_event(args["fei"]), memory.PLANT_DRUGS
    raise ValueError(f"Unknown tool {name}")


def _key(system: str, messages: list) -> str:
    raw = json.dumps({"m": MODEL, "s": system, "t": [t["name"] for t in TOOLS], "msgs": messages}, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _call_model(system: str, messages: list) -> dict:
    path = REPLAY / f"{_key(system, messages)}.json"
    if path.exists():
        return json.loads(path.read_text())
    if not settings.anthropic_api_key:
        raise LookupError("no key and no recording")
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    resp = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=system,
        tools=TOOLS,
        messages=messages,
        output_config={"effort": "medium"},
        # Fallbacks are on: a safety decline re-runs on a fallback model instead of returning nothing.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    data = resp.to_dict()
    REPLAY.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1))
    return data


def ask(question: str, hospital: str | None = None) -> dict:
    """Answer a pharmacist's question. Returns the answer, the mode used, and the trace of tool calls with Cypher."""
    content = question if not hospital else f"[Hospital: {hospital}] {question}"
    messages: list = [{"role": "user", "content": content}]
    trace: list[dict] = []
    try:
        for _ in range(MAX_STEPS):
            resp = _call_model(SYSTEM, messages)
            blocks = resp["content"]
            if resp.get("stop_reason") == "refusal":
                return {"mode": "live", "answer": "The model declined this request.", "trace": trace}
            calls = [b for b in blocks if b["type"] == "tool_use"]
            messages.append({"role": "assistant", "content": blocks})
            if not calls:
                text = " ".join(b["text"] for b in blocks if b["type"] == "text").strip()
                return {"mode": "live" if settings.anthropic_api_key else "replay", "answer": text, "trace": trace}
            results = []
            for c in calls:
                try:
                    out, cypher = run_tool(c["name"], c["input"])
                    trace.append({"tool": c["name"], "input": c["input"], "cypher": cypher.strip(), "rows": _count(out)})
                    results.append({"type": "tool_result", "tool_use_id": c["id"], "content": json.dumps(out, default=str)})
                except Exception as e:  # noqa: BLE001  a bad tool call goes back to the model as an error, not a crash
                    results.append({"type": "tool_result", "tool_use_id": c["id"], "content": f"Error: {e}", "is_error": True})
            messages.append({"role": "user", "content": results})
        return {"mode": "live", "answer": "Stopped after too many steps without an answer.", "trace": trace}
    except LookupError:
        return graph_only(question, hospital)
    except Exception as e:  # noqa: BLE001  any model failure: answer from the graph instead of returning a 500
        import anthropic

        if not isinstance(e, anthropic.APIError):
            raise
        log.warning("model call failed (%s); answering in graph-only mode", type(e).__name__)
        out = graph_only(question, hospital)
        out["note"] = f"The model call failed ({type(e).__name__}), so this answer came from the graph-only planner."
        return out


def _count(out) -> int:
    if isinstance(out, list):
        return len(out)
    return len(out.get("alerts", out.get("decisions", []))) if isinstance(out, dict) else 1


def _known_drugs() -> list[str]:
    return [r["d"] for r in queries.run("MATCH (i:Ingredient)<-[:CONTAINS]-(:Product)<-[:MAKES]-() RETURN DISTINCT i.name AS d")]


def _find_drug(question: str) -> str | None:
    """The first drug named in the question is the one in shortage ("cisplatin is short, is carboplatin ok?")."""
    q = question.upper()
    hits = []
    for n in _known_drugs():
        m = re.search(rf"\b{re.escape(n)}\b", q) or re.search(rf"\b{re.escape(moiety(n))}\b", q)
        if m:
            # At the same position prefer the full salt name the question spells out, then the shortest name.
            hits.append((m.start(), 0 if n in q else 1, len(n), n))
    return min(hits)[3] if hits else None


def graph_only(question: str, hospital: str | None) -> dict:
    """No model available: plan with rules, run the same tools, answer from a template. Labelled as such in the UI."""
    trace: list[dict] = []

    def call(name: str, **args):
        out, cypher = run_tool(name, args)
        trace.append({"tool": name, "input": args, "cypher": cypher.strip(), "rows": _count(out)})
        return out

    fei = re.search(r"\bFEI\s*(\d{5,})|\b(\d{7,10})\b", question)
    if fei:
        fei = fei.group(1) or fei.group(2)
        rows = call("plant_impact", fei=fei)
        alerts = call("plant_alert", fei=fei)["alerts"]
        if not rows:
            return {"mode": "graph-only", "answer": f"No plant with FEI {fei} in the graph.", "trace": trace}
        hit = [f"{a['name']}: {', '.join(a['stocked_drugs_made_there']).title()}" for a in alerts if a["stocked_drugs_made_there"]]
        answer = (f"{rows[0]['plant']} makes {len(rows)} drug{'s' if len(rows) > 1 else ''} in the graph: "
                  + ", ".join(f"{r['ingredient'].title()} ({int(r['share_pct'])}% of US labels)" for r in rows[:6]) + ". "
                  + ("Exposed hospitals: " + "; ".join(hit) + "." if hit else "No demo hospital stocks these."))
        return {"mode": "graph-only", "answer": answer, "trace": trace}

    drug = _find_drug(question)
    # Only a statement of something already decided writes to memory; a question never does ("is X a safe switch?").
    switched = not question.rstrip().endswith("?") and re.search(
        r"\b(switched|have switched|are switching|chose|have chosen|went with|are going with|moved|decided on)\b", question, re.I)
    if drug and hospital and switched:
        rest = question[question.upper().index(moiety(drug)) + len(moiety(drug)):]
        sub = _find_drug(rest)
        if sub and moiety(sub) != moiety(drug):
            saved = call("record_decision", hospital=hospital, drug=drug, substitute=sub, reason=question.strip())
            shared = next((t["shared_plants"] for t in call("substitute_trap", drug=drug) if t["substitute"] == sub), [])
            note = (f" It shares {len(shared)} plant{'s' if len(shared) != 1 else ''} with {drug.title()}: "
                    + ", ".join(f"{p['plant']} (FEI {p['fei']})" for p in shared) + ".") if shared else \
                f" It shares no manufacturing plant with {drug.title()}."
            return {"mode": "graph-only", "trace": trace,
                    "answer": f"Recorded for this hospital: {drug.title()} switched to {sub.title()} on {saved['at'][:10]}.{note}"}
    if not drug:
        hint = "Name a drug from the shortage list or a plant FEI number, and I will trace it."
        return {"mode": "graph-only", "answer": hint, "trace": trace}
    plants = call("plants_for_drug", drug=drug)
    traps = [t for t in call("substitute_trap", drug=drug) if t["shared_plants"]]
    safe = call("safe_substitutes", drug=drug)
    parts = [f"{drug.title()} is made in {len(plants)} plant{'s' if len(plants) != 1 else ''}."]
    flagged = [p for p in plants if p["import_alert_dates"]]
    if flagged:
        parts.append("Under FDA Import Alert 66-40: " + "; ".join(
            f"{p['plant']} (FEI {p['fei']}, listed {', '.join(p['import_alert_dates'])})" for p in flagged) + ".")
    for t in traps[:3]:
        n = len(t["shared_plants"])
        plants_txt = ", ".join(f"{p['plant']} (FEI {p['fei']})" for p in t["shared_plants"])
        parts.append(f"{t['substitute'].title()} shares {n} plant{'s' if n > 1 else ''} with it: {plants_txt}.")
    if safe:
        parts.append("Same-class options with no shared plant: " + ", ".join(
            f"{s['substitute'].title()} ({s['labels']} labels{', short now' if s['short_now'] else ''})" for s in safe[:4]) + ".")
    else:
        parts.append("Every same-class substitute in the graph shares a plant with it.")
    parts.append("Same class is not the same as interchangeable; the pharmacist decides.")
    if hospital:
        mem = call("hospital_memory", hospital=hospital)
        past = [d for d in mem["decisions"] if d["drug"] == drug]
        if past:
            last = past[0]
            parts.append(f"Last time, {mem['name']} switched {drug.title()} to {last['substitute'].title()} ({last['at'][:10]}).")
    return {"mode": "graph-only", "answer": " ".join(parts), "trace": trace}
