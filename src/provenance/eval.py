"""How often does a similarity-picked substitute come from the same plant as the drug that is short?

For every scoped drug with a current shortage, take the plain-RAG top substitute and check it against the graph.
Writes eval/results.json and eval/results.md. Every number in the README comes from here.
"""

import json
import logging

from .baseline import moiety, rag_substitutes
from .config import ROOT
from .queries import run

log = logging.getLogger("eval")
OUT = ROOT / "eval"

SHORT_DRUGS = """
MATCH (i:Ingredient)<-[:CONTAINS]-(:Product)-[:IN_SHORTAGE]->(:Shortage {status: 'Current'})
WHERE i.vec IS NOT NULL
RETURN DISTINCT i.name AS drug ORDER BY drug
"""

SHARED = """
MATCH (a:Ingredient {name: $a})<-[:CONTAINS]-(:Product)<-[:MAKES]-(f:Facility)-[:MAKES]->(:Product)-[:CONTAINS]->(b:Ingredient {name: $b})
OPTIONAL MATCH (f)-[:FLAGGED]->(x:Action)
RETURN f.name AS plant, f.fei AS fei, f.country AS country, count(x) > 0 AS flagged
"""

SUB_SHORT = """
MATCH (b:Ingredient {name: $b})<-[:CONTAINS]-(:Product)-[:IN_SHORTAGE]->(s:Shortage {status: 'Current'})
RETURN count(DISTINCT s) AS n
"""

SAFE_EXISTS = """
MATCH (i:Ingredient {name: $drug})-[:IN_CLASS]->(:Class)<-[:IN_CLASS]-(alt:Ingredient)
WHERE alt <> i
OPTIONAL MATCH (i)<-[:CONTAINS]-(:Product)<-[:MAKES]-(f:Facility)-[:MAKES]->(:Product)-[:CONTAINS]->(alt)
WITH alt, count(f) AS shared
RETURN collect(CASE WHEN shared = 0 THEN alt.name END) AS independent, count(alt) AS candidates
"""


def evaluate() -> dict:
    drugs = [r["drug"] for r in run(SHORT_DRUGS)]
    rows = []
    for d in drugs:
        subs = rag_substitutes(d, k=1)
        if not subs:
            continue
        b = subs[0]["substitute"]
        shared = run(SHARED, a=d, b=b)
        short = run(SUB_SHORT, b=b)[0]["n"]
        safe = run(SAFE_EXISTS, drug=d)[0]
        rows.append({
            "drug": d, "rag_substitute": b, "similarity": subs[0]["score"],
            "shared_plants": shared, "rag_sub_also_short": short > 0,
            "trap": bool(shared) or short > 0,
            "graph_independent_options": [x for x in safe["independent"] if x and moiety(x) != moiety(d)],
            "graph_candidates": safe["candidates"],
        })
    n = len(rows)
    summary = {
        "drugs_in_shortage_evaluated": n,
        "rag_sub_shares_a_plant": sum(1 for r in rows if r["shared_plants"]),
        "rag_sub_shares_flagged_plant": sum(1 for r in rows if any(p["flagged"] for p in r["shared_plants"])),
        "rag_sub_itself_in_shortage": sum(1 for r in rows if r["rag_sub_also_short"]),
        "rag_trap_total": sum(1 for r in rows if r["trap"]),
        "graph_found_independent_substitute": sum(1 for r in rows if r["graph_independent_options"]),
    }
    OUT.mkdir(exist_ok=True)
    (OUT / "results.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=1))
    (OUT / "results.md").write_text(_md(summary, rows))
    log.info("%s", summary)
    return summary


def _md(s: dict, rows: list[dict]) -> str:
    n = s["drugs_in_shortage_evaluated"] or 1
    lines = [
        "| Measure | Count | Share |", "|---|---|---|",
        *[f"| {k.replace('_', ' ')} | {v} | {round(100 * v / n)}% |" for k, v in s.items() if k != "drugs_in_shortage_evaluated"],
        f"\nDrugs in current shortage evaluated: {s['drugs_in_shortage_evaluated']}\n",
        "| Drug short | RAG substitute | Shared plants | Sub also short | Plant-independent options (graph) |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        plants = "; ".join(f"{p['plant']} ({p['country']}){' FLAGGED' if p['flagged'] else ''}" for p in r["shared_plants"]) or "none"
        opts = ", ".join(r["graph_independent_options"][:4]) or "none"
        lines.append(f"| {r['drug']} | {r['rag_substitute']} | {plants} | {'yes' if r['rag_sub_also_short'] else 'no'} | {opts} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")
    evaluate()


if __name__ == "__main__":
    main()
