"""The agent: it decides which graph queries to run, and every fact in its answer comes from a tool result.

Every answer is computed live against FalkorDB; nothing is recorded or replayed.
- live: an OpenAI key (PV_OPENAI_API_KEY) or an Anthropic key is set, so the model picks the tools.
- graph-only: no key; a rule-based planner picks the same tools and writes the answer from their rows.
"""

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor

from . import memory, queries
from .baseline import moiety
from .config import settings

log = logging.getLogger("agent")
MODEL = "claude-opus-5-5"
MAX_STEPS = 4
MAX_TOOL_TEXT = 6000  # characters of tool output sent back to the model per call; keeps each request small

_budget = {"day": "", "used": 0}


def _spend_one() -> bool:
    """Count one model call against today's cap. False once the cap is reached."""
    today = time.strftime("%Y-%m-%d", time.gmtime())
    if _budget["day"] != today:
        _budget.update(day=today, used=0)
    if _budget["used"] >= settings.model_calls_per_day:
        return False
    _budget["used"] += 1
    return True

SYSTEM = """You help a hospital pharmacist during drug shortages. You answer from a FalkorDB graph of FDA public records: \
drug labels and the plants named on them, the FDA establishment registry, the FDA drug shortage list, and FDA Import Alert 66-40.

Which tools to call:
- A drug is short, or a substitute is proposed: call check_substitute once (substitute empty if none is named). If a \
hospital is named, call hospital_memory in the same turn. That is enough to answer; call nothing else.
- A plant (FEI number) is named: call plant_impact and plant_alert in the same turn.
- Riskiest plants, bridges, systemic or chokepoint plants: call bridge_plants. Use shortage_exposure only when asked which \
plants make the most of today's shortage-list drugs.
- The pharmacist says they switched to or chose a substitute: call record_decision and check_substitute in the same turn.

What every substitute answer must say, in this order:
1. Whether the proposed substitute shares a manufacturing plant with the drug that is short, naming each plant in \
plants_shared_with_substitute with its FEI number.
2. Any shared plant that has an FDA Import Alert 66-40 date: say so with the date. This is the most important fact; never \
leave it out.
3. Same-class options from same_class_with_no_shared_plant, or say there are none.
4. That same class does not mean interchangeable for a patient; the pharmacist decides.

Rules: never name a plant, FEI number, date or count that a tool did not return. Label counts are not market share. Answer \
in short plain sentences, no headings, no emoji."""


def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


DRUG = {"drug": {"type": "string", "description": "Substance name as on FDA labels, e.g. CISPLATIN or MIDAZOLAM HYDROCHLORIDE."}}
HOSP = {"hospital": {"type": "string", "enum": list(memory.DEMO_HOSPITALS), "description": "Hospital tenant slug."}}
FEI = {"fei": {"type": "string", "description": "FDA Facility Establishment Identifier of the plant."}}

def _tool(name: str, description: str, props: dict, required: list[str]) -> dict:
    return {"name": name, "description": description, "input_schema": _schema(props, required), "strict": True}


DECISION = {**HOSP, **DRUG, "substitute": {"type": "string"}, "reason": {"type": "string"}}

TOOLS = [
    _tool("check_substitute", "Everything about switching from a drug in shortage to a proposed substitute, in one call: the "
          "plants that make the drug (with import alert dates), the plants the substitute shares with it, the other "
          "same-class drugs that share plants, and the same-class options with no shared plant. Pass an empty substitute "
          "when none is proposed.",
          {**DRUG, "substitute": {"type": "string", "description": "Proposed substitute, or empty."}}, ["drug", "substitute"]),
    _tool("plants_for_drug", "Every plant that manufactures the drug, with FEI, country, label count and FDA import alert dates.",
          DRUG, ["drug"]),
    _tool("substitute_trap", "Same-class substitutes for the drug and the manufacturing plants each shares with it.",
          DRUG, ["drug"]),
    _tool("safe_substitutes", "Same-class substitutes that share no manufacturing plant with the drug, "
          "with label count, flagged plants and shortage status.", DRUG, ["drug"]),
    _tool("plant_impact", "Every drug a plant manufactures, its share of US labels, and whether it is on the shortage list now.",
          FEI, ["fei"]),
    _tool("shortage_exposure", "Plants ranked by how much of today's shortage-list drugs they make. Not for bridges.",
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
    if name == "check_substitute":
        return check_substitute(args["drug"], args.get("substitute", "")), queries.TRAP
    if name == "plants_for_drug":
        return queries.drug_plants(args["drug"]), queries.DRUG_PLANTS
    if name == "substitute_trap":
        return _other(queries.trap(args["drug"]), args["drug"]), queries.TRAP
    if name == "safe_substitutes":
        return _other(queries.safe(args["drug"]), args["drug"]), queries.SAFE
    if name == "plant_impact":
        return queries.plant_down(args["fei"]), queries.PLANT_DOWN
    if name == "shortage_exposure":
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


def _call_model(system: str, messages: list) -> dict:
    if not settings.anthropic_api_key:
        raise LookupError("no model key")
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
    return resp.to_dict()


def ask(question: str, hospital: str | None = None) -> dict:
    """Answer a pharmacist's question, timed end to end."""
    start = time.perf_counter()
    out = _ask(question, hospital)
    out["elapsed_ms"] = round((time.perf_counter() - start) * 1000)
    return out


def check_substitute(drug: str, substitute: str) -> dict:
    """The three substitute lookups at once, shaped so a small model reads the answer straight off it."""
    with ThreadPoolExecutor(max_workers=3) as pool:
        plants, trap, safe = (pool.submit(f, drug) for f in (queries.drug_plants, queries.trap, queries.safe))
    rows = _other(trap.result(), drug)
    sub = substitute.strip().upper()
    match = next((r for r in rows if sub and (r["substitute"] == sub or moiety(r["substitute"]) == moiety(sub))), None)
    return {
        "drug": drug.upper(),
        "plants_making_drug": plants.result(),
        "substitute": sub or None,
        "substitute_in_same_class": match is not None,
        "plants_shared_with_substitute": match["shared_plants"] if match else [],
        "other_same_class_drugs_sharing_plants": [{"drug": r["substitute"], "shared_plants": len(r["shared_plants"])}
                                                  for r in rows if r is not match and r["shared_plants"]],
        "same_class_with_no_shared_plant": _other(safe.result(), drug),
    }


def _run_calls(calls: list[tuple[str, str, dict]], trace: list[dict]) -> dict[str, str]:
    """Run the model's tool calls at once (they are independent reads) and return each result as JSON text by call id."""
    def one(call):
        cid, name, args = call
        try:
            out, cypher = run_tool(name, args)
            return cid, {"tool": name, "input": args, "cypher": cypher.strip(), "rows": _count(out)}, json.dumps(out, default=str)
        except Exception as e:  # noqa: BLE001  a bad tool call goes back to the model as an error, not a crash
            return cid, None, f"Error: {e}"

    with ThreadPoolExecutor(max_workers=max(1, len(calls))) as pool:
        done = list(pool.map(one, calls))
    trace.extend(t for _, t, _ in done if t)
    return {cid: text for cid, _, text in done}


def _ask_openai(content: str, trace: list[dict]) -> dict:
    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key)
    tools = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                               "parameters": t["input_schema"], "strict": True}} for t in TOOLS]
    messages: list = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}]
    for _ in range(MAX_STEPS):
        if not _spend_one():
            raise LookupError("daily model budget used")
        msg = client.chat.completions.create(model=settings.openai_model, messages=messages, tools=tools,
                                             max_completion_tokens=600).choices[0].message
        if not msg.tool_calls:
            return {"mode": "live", "model": settings.openai_model, "answer": (msg.content or msg.refusal or "").strip(), "trace": trace}
        messages.append(msg.model_dump(exclude_none=True))
        calls = [(c.id, c.function.name, json.loads(c.function.arguments or "{}")) for c in msg.tool_calls]
        results = _run_calls(calls, trace)
        messages += [{"role": "tool", "tool_call_id": cid, "content": text[:MAX_TOOL_TEXT]} for cid, text in results.items()]
    return {"mode": "live", "model": settings.openai_model, "answer": "Stopped after too many steps without an answer.", "trace": trace}


def _ask_anthropic(content: str, trace: list[dict]) -> dict:
    messages: list = [{"role": "user", "content": content}]
    for _ in range(MAX_STEPS):
        if not _spend_one():
            raise LookupError("daily model budget used")
        resp = _call_model(SYSTEM, messages)
        blocks = resp["content"]
        if resp.get("stop_reason") == "refusal":
            return {"mode": "live", "model": MODEL, "answer": "The model declined this request.", "trace": trace}
        calls = [(b["id"], b["name"], b["input"]) for b in blocks if b["type"] == "tool_use"]
        messages.append({"role": "assistant", "content": blocks})
        if not calls:
            text = " ".join(b["text"] for b in blocks if b["type"] == "text").strip()
            return {"mode": "live", "model": MODEL, "answer": text, "trace": trace}
        results = _run_calls(calls, trace)
        messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": cid, "content": text}
                                                     for cid, text in results.items()]})
    return {"mode": "live", "model": MODEL, "answer": "Stopped after too many steps without an answer.", "trace": trace}


def _ask(question: str, hospital: str | None) -> dict:
    """Returns the answer, the mode used, and the trace of tool calls with Cypher."""
    content = question if not hospital else f"[Hospital: {hospital}] {question}"
    trace: list[dict] = []
    try:
        if settings.openai_api_key:
            return _ask_openai(content, trace)
        if settings.anthropic_api_key:
            return _ask_anthropic(content, trace)
    except LookupError:
        out = graph_only(question, hospital)
        out["note"] = "Today's model budget is used up, so this answer came from the graph-only planner."
        return out
    except Exception as e:  # noqa: BLE001  any model failure: answer from the graph instead of returning a 500
        if not _is_model_error(e):
            raise
        log.warning("model call failed (%s); answering in graph-only mode", type(e).__name__)
        out = graph_only(question, hospital)
        out["note"] = f"The model call failed ({type(e).__name__}), so this answer came from the graph-only planner."
        return out
    return graph_only(question, hospital)


def _is_model_error(e: Exception) -> bool:
    import anthropic
    import openai

    return isinstance(e, (anthropic.APIError, openai.OpenAIError))


def _count(out) -> int:
    if isinstance(out, list):
        return len(out)
    return len(out.get("alerts", out.get("decisions", []))) if isinstance(out, dict) else 1


_DRUG_NAMES: list[str] = []


def warm() -> None:
    """Read the drug list and open one pooled connection per parallel lookup, so no visitor pays for the handshakes."""
    _known_drugs()
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: queries.run("RETURN 1 AS ok"), range(4)))


def _known_drugs() -> list[str]:
    # The drug list only changes when the graph is reloaded, so read it once per process.
    if not _DRUG_NAMES:
        _DRUG_NAMES.extend(r["d"] for r in queries.run(
            "MATCH (i:Ingredient)<-[:CONTAINS]-(:Product)<-[:MAKES]-() RETURN DISTINCT i.name AS d"))
    return _DRUG_NAMES


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

    if re.search(r"\b(riskiest|risk|bridge|bridges|chokepoint|systemic|most exposed)\b", question, re.I) and not _find_drug(question):
        rows = call("bridge_plants", limit=5)
        lines = [f"{r['plant']} (FEI {r['fei']}, {r['country']}): {r['drugs_made']} drugs across {r['classes']} classes"
                 for r in rows]
        return {"mode": "graph-only", "trace": trace,
                "answer": "Plants that tie the most separate drug supply chains together, by exact betweenness centrality: "
                          + "; ".join(lines) + ". A failure at one of them spreads shortages across drug classes."}

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
    # The lookups are independent, so they go out together: one network round trip instead of four.
    calls = {n: {"drug": drug} for n in ("plants_for_drug", "substitute_trap", "safe_substitutes")}
    if hospital:
        calls["hospital_memory"] = {"hospital": hospital}
    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        jobs = {n: pool.submit(run_tool, n, a) for n, a in calls.items()}
    for n, job in jobs.items():
        out, cypher = job.result()
        trace.append({"tool": n, "input": calls[n], "cypher": cypher.strip(), "rows": _count(out)})
    plants = jobs["plants_for_drug"].result()[0]
    traps = [t for t in jobs["substitute_trap"].result()[0] if t["shared_plants"]]
    safe = jobs["safe_substitutes"].result()[0]
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
        mem = jobs["hospital_memory"].result()[0]
        past = [d for d in mem["decisions"] if d["drug"] == drug]
        if past:
            last = past[0]
            parts.append(f"Last time, {mem['name']} switched {drug.title()} to {last['substitute'].title()} ({last['at'][:10]}).")
    return {"mode": "graph-only", "answer": " ".join(parts), "trace": trace}
