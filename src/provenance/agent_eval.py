"""Scores the deployed agent against the graph it reads from.

For each drug on the current shortage list, ask the live /api/ask the same plain question and check the answer
against FalkorDB: every FEI and import-alert date it cites must exist in the graph for that drug or its class,
every flagged plant that makes the drug must be named with its date, and when the graph has a same-class option
with no shared plant, the answer must name at least one. Writes eval/agent.json and eval/agent.md.
"""

import json
import re
import time
from pathlib import Path

import httpx

from . import agent, queries
from .eval import SHORT_DRUGS

QUESTION = "{drug} is on the shortage list. What can we switch to, and is anything risky?"
FEI = re.compile(r"\b\d{6,10}\b")
DATE = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")


def truth(drug: str) -> dict:
    sub = agent.check_substitute(drug, "")
    plants = sub["plants_making_drug"]
    feis = {p["fei"] for p in plants if p.get("fei")}
    dates = {d for p in plants for d in p.get("import_alert_dates", [])}
    for r in queries.trap(drug):
        for p in r["shared_plants"]:
            feis.add(p["fei"])
            dates.update(re.findall(DATE, " ".join(p.get("flags", []))))
    return {
        "feis": feis,
        "dates": dates,
        "flagged": {p["fei"]: p["import_alert_dates"] for p in plants if p.get("import_alert_dates")},
        "safe": [r["substitute"] for r in sub["same_class_with_no_shared_plant"]],
    }


def score(answer: str, t: dict) -> dict:
    up = answer.upper()
    cited_feis = set(FEI.findall(answer))
    cited_dates = set(DATE.findall(answer))
    missing = [f for f, ds in t["flagged"].items() if f not in answer or not any(d in answer for d in ds)]
    return {
        "invented_feis": sorted(cited_feis - t["feis"]),
        "invented_dates": sorted(cited_dates - t["dates"]),
        "flagged_missed": missing,
        "names_safe_option": (not t["safe"]) or any(agent.moiety(s) in up or s in up for s in t["safe"]),
    }


def main(base: str = "https://provenance-imzk.onrender.com") -> dict:
    drugs = [r["drug"] for r in queries.run(SHORT_DRUGS)]
    rows = []
    with httpx.Client(base_url=base, timeout=120) as http:
        for d in drugs:
            t = truth(d)
            r = http.post("/api/ask", json={"question": QUESTION.format(drug=d.title())}).json()
            s = score(r["answer"], t)
            rows.append({"drug": d, "mode": r["mode"], "model": r.get("model"), "elapsed_ms": r["elapsed_ms"],
                         "tools": [x["tool"] for x in r["trace"]], **s, "answer": r["answer"]})
            print(f"{d:32} {r['mode']:10} {r['elapsed_ms']:>6} ms  invented={len(s['invented_feis']) + len(s['invented_dates'])}"
                  f"  missed={len(s['flagged_missed'])}  safe={s['names_safe_option']}")
            time.sleep(0.5)
    live = [r for r in rows if r["mode"] == "live"]
    summary = {
        "asked": len(rows),
        "answered_live": len(live),
        "model": live[0]["model"] if live else None,
        "no_invented_fei_or_date": sum(not r["invented_feis"] and not r["invented_dates"] for r in live),
        "every_flagged_plant_named_with_date": sum(not r["flagged_missed"] for r in live),
        "names_a_no_shared_plant_option": sum(r["names_safe_option"] for r in live),
        "median_seconds": sorted(r["elapsed_ms"] for r in live)[len(live) // 2] / 1000 if live else None,
    }
    out = Path("eval")
    (out / "agent.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=2), encoding="utf8")
    n, s = len(live), summary
    md = ["# Agent eval", "", f"Live agent at {base}, model {summary['model']}, one question per drug on today's shortage list:",
          "", f"> {QUESTION.format(drug='<drug>')}", "", "Each answer is checked against the graph.", "",
          "| Check | Passed |", "|---|---|",
          f"| Cites no FEI or import-alert date that is not in the graph | {s['no_invented_fei_or_date']} of {n} |",
          f"| Names every flagged plant that makes the drug, with its alert date | {s['every_flagged_plant_named_with_date']} of {n} |",
          f"| Names a same-class option with no shared plant, when one exists | {s['names_a_no_shared_plant_option']} of {n} |",
          "", f"Median time per answer: {summary['median_seconds']} s.", "",
          "| Drug | Tools | Invented | Flagged missed | Safe option named |", "|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['drug']} | {', '.join(r['tools'])} | {', '.join(r['invented_feis'] + r['invented_dates']) or 'none'} "
                  f"| {', '.join(r['flagged_missed']) or 'none'} | {'yes' if r['names_safe_option'] else 'no'} |")
    (out / "agent.md").write_text("\n".join(md) + "\n", encoding="utf8")
    return summary


if __name__ == "__main__":
    print(json.dumps(main(), indent=2))
