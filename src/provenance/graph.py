"""Load data/snapshot.json into FalkorDB."""

import json
import logging
import re

import redis

from .build import SNAPSHOT
from .config import connect, settings

log = logging.getLogger("graph")

MANUFACTURE_OPS = {"manufacture", "api manufacture", "sterilize"}
COUNTRY = {"INDIA": "IND", "CHINA": "CHN", "ITALY": "ITA", "GERMANY": "DEU", "SPAIN": "ESP", "KOREA": "KOR"}
NOISE = {"limited", "ltd", "private", "pvt", "inc", "llc", "co", "corp", "corporation", "company", "the", "pharmaceuticals",
         "pharmaceutical", "pharma", "laboratories", "labs", "industries", "sa", "spa", "gmbh", "ag", "plc", "unit"}


def graph():
    return connect().select_graph(settings.graph_name)


def _norm(name: str) -> str:
    words = re.findall(r"[a-z0-9]+", name.lower())
    return " ".join(w for w in words if w not in NOISE)


def _tokens(addr: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", addr.lower())}


def _batches(rows, n=500):
    for i in range(0, len(rows), n):
        yield rows[i : i + n]


def load(snap: dict | None = None) -> dict:
    snap = snap or json.loads(SNAPSHOT.read_text())
    g = graph()
    try:
        g.delete()
    except redis.exceptions.ResponseError:
        pass  # no graph yet
    g = graph()
    for label, prop in [("Product", "ndc"), ("Ingredient", "name"), ("Class", "name"), ("Company", "name"),
                        ("Facility", "duns"), ("Shortage", "package_ndc"), ("Action", "key")]:
        g.query(f"CREATE INDEX FOR (n:{label}) ON (n.{prop})")

    products = snap["products"]
    for b in _batches(products):
        g.query(
            """UNWIND $rows AS r
               MERGE (p:Product {ndc: r.ndc})
               SET p.name = r.name, p.generic = r.generic, p.route = r.route, p.form = r.form, p.setid = r.setid
               MERGE (c:Company {name: r.labeler})
               MERGE (p)-[:LABELED_BY]->(c)
               WITH p, r
               UNWIND r.substances AS s
               MERGE (i:Ingredient {name: s})
               MERGE (p)-[:CONTAINS]->(i)""",
            {"rows": b},
        )
    # Class membership only from single-ingredient products, so a combo product does not drag its partner into a class.
    ic = sorted({(p["substances"][0], c) for p in products if len(p["substances"]) == 1 for c in p["classes"]})
    for b in _batches([{"i": i, "c": c.replace(" [EPC]", "")} for i, c in ic]):
        g.query("UNWIND $rows AS r MATCH (i:Ingredient {name: r.i}) MERGE (c:Class {name: r.c}) MERGE (i)-[:IN_CLASS]->(c)",
                {"rows": b})

    reg = {r["duns"]: r for r in snap["registry"] if r["duns"]}
    known = {p["ndc"] for p in products}
    makes, plants = [], {}
    for m in snap["makes"]:
        r = reg.get(m["duns"], {})
        plants[m["duns"]] = {"duns": m["duns"], "name": r.get("name") or m["name"], "fei": r.get("fei", ""),
                             "country": r.get("country", ""), "address": r.get("address", "")}
        makes += [{"duns": m["duns"], "ndc": n, "op": m["op"]} for n in m["ndcs"] if n in known]
    g.query("UNWIND $rows AS r MERGE (f:Facility {duns: r.duns}) SET f.name = r.name, f.fei = r.fei, "
            "f.country = r.country, f.address = r.address", {"rows": list(plants.values())})
    # MAKES: the plant manufactures or sterilises the product, so its failure stops supply.
    # HANDLES: repack, relabel, pack, label or test only; a different supply risk, kept out of the trap logic.
    for rel, subset in (("MAKES", [m for m in makes if m["op"] in MANUFACTURE_OPS]),
                        ("HANDLES", [m for m in makes if m["op"] not in MANUFACTURE_OPS])):
        for b in _batches(subset):
            g.query(f"""UNWIND $rows AS r MATCH (f:Facility {{duns: r.duns}}), (p:Product {{ndc: r.ndc}})
                       MERGE (f)-[m:{rel}]->(p) SET m.ops = coalesce(m.ops, []) + r.op""", {"rows": b})

    shorts = []
    for s in snap["shortages"]:
        for n in s["product_ndcs"]:
            if n in known:
                shorts.append({**s, "ndc": n, "category": ", ".join(s["category"])})
    for b in _batches(shorts):
        g.query("""UNWIND $rows AS r MATCH (p:Product {ndc: r.ndc})
                   MERGE (s:Shortage {package_ndc: r.package_ndc})
                   SET s.status = r.status, s.availability = r.availability, s.reason = r.reason,
                       s.posted = r.posted, s.company = r.company, s.category = r.category
                   MERGE (p)-[:IN_SHORTAGE]->(s)""", {"rows": b})

    flagged, unmatched = _match_alerts(snap["import_alert_6640"], snap["registry"], set(plants))
    g.query("""UNWIND $rows AS r MATCH (f:Facility {duns: r.duns})
               MERGE (a:Action {key: r.key}) SET a.type = 'FDA Import Alert 66-40', a.date = r.date, a.firm = r.firm
               MERGE (f)-[:FLAGGED]->(a)""", {"rows": flagged})

    stats = {
        "products": len(products), "facilities": len(plants),
        "makes_edges": sum(1 for m in makes if m["op"] in MANUFACTURE_OPS),
        "handles_edges": sum(1 for m in makes if m["op"] not in MANUFACTURE_OPS),
        "shortage_links": len(shorts), "import_alert_matches": len(flagged),
        "import_alert_firms_unmatched": unmatched,
    }
    log.info("loaded %s", stats)
    return stats


def _match_alerts(alerts: list[dict], registry: list[dict], in_graph: set[str]) -> tuple[list[dict], int]:
    """Match each import-alert firm to the one registered site with the same name, country, and most shared
    address words. Firms with several sites (Intas has seven) make the address the deciding field."""
    by_name: dict[str, list[dict]] = {}
    for r in registry:
        by_name.setdefault(_norm(r["name"]), []).append(r)
    out, unmatched = [], 0
    for a in alerts:
        country = COUNTRY.get(a["country"], a["country"][:3])
        words = _tokens(a["address"])
        scored = [(len(words & _tokens(r["address"])), r) for r in by_name.get(_norm(a["name"]), []) if r["country"] == country]
        scored = [s for s in scored if s[0] >= 2]
        if not scored:
            unmatched += 1
            continue
        best = max(scored, key=lambda s: s[0])[1]
        if best["duns"] in in_graph:
            out.append({"duns": best["duns"], "key": f"IA6640:{best['fei']}:{a['date']}", "date": a["date"], "firm": a["name"]})
    return out, unmatched


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")
    load()


if __name__ == "__main__":
    main()
