"""Per-hospital memory. Each hospital is its own FalkorDB graph in the same server, so one tenant never sees another's
decisions. The supply graph is shared; a hospital graph only records what that hospital stocks and what it decided.

Hospitals here are synthetic demo tenants; every drug, plant and FDA action they point at is real.
"""

from datetime import datetime, timezone

from falkordb import FalkorDB

from .config import settings

PREFIX = "hospital:"

# Synthetic tenants. Formularies list real drugs that are in the supply graph.
DEMO_HOSPITALS = {
    "oncology-centre": {
        "name": "Demo oncology centre",
        "stocks": ["CISPLATIN", "CARBOPLATIN", "OXALIPLATIN", "MORPHINE SULFATE", "HYDROMORPHONE HYDROCHLORIDE"],
    },
    "surgical-hospital": {
        "name": "Demo surgical hospital",
        "stocks": ["ROCURONIUM BROMIDE", "MIDAZOLAM HYDROCHLORIDE", "DEXMEDETOMIDINE HYDROCHLORIDE",
                   "BUPIVACAINE HYDROCHLORIDE", "LIDOCAINE HYDROCHLORIDE", "KETOROLAC TROMETHAMINE"],
    },
    "childrens-hospital": {
        "name": "Demo children's hospital",
        "stocks": ["METHYLPHENIDATE HYDROCHLORIDE", "LISDEXAMFETAMINE DIMESYLATE", "LORAZEPAM", "FUROSEMIDE",
                   "MIDAZOLAM HYDROCHLORIDE"],
    },
}


def _db() -> FalkorDB:
    return FalkorDB(host=settings.falkordb_host, port=settings.falkordb_port)


def _g(hospital: str):
    if hospital not in DEMO_HOSPITALS:
        raise KeyError(f"Unknown hospital '{hospital}'. Known: {', '.join(DEMO_HOSPITALS)}")
    return _db().select_graph(PREFIX + hospital)


def _rows(res) -> list[dict]:
    cols = [h[1] if isinstance(h, (list, tuple)) else h for h in res.header]
    return [dict(zip(cols, r)) for r in res.result_set]


def seed(reset: bool = False) -> None:
    db = _db()
    existing = set(db.list_graphs())
    for slug, h in DEMO_HOSPITALS.items():
        if PREFIX + slug in existing and not reset:
            continue
        g = db.select_graph(PREFIX + slug)
        if PREFIX + slug in existing:
            g.delete()
            g = db.select_graph(PREFIX + slug)
        g.query("MERGE (h:Hospital {slug: $slug}) SET h.name = $name "
                "WITH h UNWIND $stocks AS d MERGE (x:Drug {name: d}) MERGE (h)-[:STOCKS]->(x)",
                {"slug": slug, "name": h["name"], "stocks": h["stocks"]})


def hospitals() -> list[dict]:
    return [{"slug": s, "name": h["name"]} for s, h in DEMO_HOSPITALS.items()]


def profile(hospital: str) -> dict:
    g = _g(hospital)
    stocks = [r["drug"] for r in _rows(g.ro_query("MATCH (:Hospital)-[:STOCKS]->(d:Drug) RETURN d.name AS drug ORDER BY drug"))]
    decisions = _rows(g.ro_query(
        "MATCH (:Hospital)-[:DECIDED]->(x:Decision) RETURN x.drug AS drug, x.substitute AS substitute, "
        "x.reason AS reason, x.at AS at ORDER BY x.at DESC"))
    return {"hospital": hospital, "name": DEMO_HOSPITALS[hospital]["name"], "stocks": stocks, "decisions": decisions}


def record_decision(hospital: str, drug: str, substitute: str, reason: str) -> dict:
    """Store a substitution decision in the hospital's own graph. Superseded decisions for the same drug are kept,
    linked by REPLACED_BY, so the history of what was switched and why stays queryable."""
    g = _g(hospital)
    at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {"drug": drug.upper(), "sub": substitute.upper(), "reason": reason, "at": at}
    g.query("MATCH (h:Hospital) CREATE (h)-[:DECIDED]->(:Decision {drug: $drug, substitute: $sub, reason: $reason, at: $at})", params)
    # The older current decision for this drug steps aside: it stays in the graph, linked to the one that replaced it.
    g.query("""MATCH (h:Hospital)-[old:DECIDED]->(prev:Decision {drug: $drug}), (h)-[:DECIDED]->(new:Decision {drug: $drug, at: $at})
               WHERE prev <> new
               CREATE (prev)-[:REPLACED_BY]->(new) DELETE old""", params)
    return {"hospital": hospital, "drug": drug.upper(), "substitute": substitute.upper(), "at": at}


def forget(hospital: str) -> None:
    _g(hospital).query("MATCH (d:Decision) DETACH DELETE d")


PLANT_DRUGS = "MATCH (f:Facility {fei: $fei})-[:MAKES]->(:Product)-[:CONTAINS]->(i:Ingredient) RETURN DISTINCT i.name AS drug"


def plant_event(fei: str) -> dict:
    """An FDA action hits one plant. Each hospital gets only the alerts that touch its own stock and its own past
    decisions: the same event reads differently in every tenant."""
    supply = _db().select_graph(settings.graph_name)
    made_there = {r["drug"] for r in _rows(supply.ro_query(PLANT_DRUGS, {"fei": fei}))}
    plant = _rows(supply.ro_query("MATCH (f:Facility {fei: $fei}) RETURN f.name AS name, f.country AS country", {"fei": fei}))
    out = []
    for slug in DEMO_HOSPITALS:
        p = profile(slug)
        stocked = [d for d in p["stocks"] if d in made_there]
        exposed_decisions = [d for d in p["decisions"] if d["substitute"] in made_there]
        out.append({"hospital": slug, "name": p["name"], "stocked_drugs_made_there": stocked,
                    "substitutes_you_chose_made_there": exposed_decisions})
    return {"fei": fei, "plant": plant[0] if plant else None, "alerts": out}
