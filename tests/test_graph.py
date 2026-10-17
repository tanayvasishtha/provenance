"""Runs the product's queries against a FalkorDB loaded from the committed snapshot. Skipped when no FalkorDB is reachable."""

import pytest

from provenance import agent, memory, queries
from provenance.graph import graph, load

INTAS = "3004011473"


@pytest.fixture(scope="module", autouse=True)
def loaded():
    try:
        graph().query("RETURN 1")
    except Exception:
        pytest.skip("FalkorDB not reachable on PV_FALKORDB_HOST:PV_FALKORDB_PORT")
    if not queries.run("MATCH (f:Facility {fei: $fei}) RETURN f.fei AS fei", fei=INTAS):
        load()
    memory.seed()


def test_carboplatin_is_a_trap_for_cisplatin_through_intas():
    rows = {r["substitute"]: r for r in queries.trap("cisplatin")}
    shared = {p["fei"]: p for p in rows["CARBOPLATIN"]["shared_plants"]}
    assert INTAS in shared
    assert any("06/01/2023" in f for f in shared[INTAS]["flags"])


def test_second_intas_site_gets_its_own_import_alert():
    plants = {p["fei"]: p for p in queries.drug_plants("cisplatin")}
    assert plants[INTAS]["import_alert_dates"] == ["06/01/2023"]
    assert plants["3003157498"]["import_alert_dates"] == ["11/14/2023"]


def test_plant_down_lists_every_drug_and_marks_current_shortages():
    rows = {r["ingredient"]: r for r in queries.plant_down(INTAS)}
    assert {"CISPLATIN", "CARBOPLATIN", "OXALIPLATIN"} <= set(rows)
    assert rows["CARBOPLATIN"]["short_now"] > 0


def test_one_event_reads_differently_per_hospital():
    alerts = {a["hospital"]: a for a in memory.plant_event(INTAS)["alerts"]}
    assert "CISPLATIN" in alerts["oncology-centre"]["stocked_drugs_made_there"]
    assert "CISPLATIN" not in alerts["surgical-hospital"]["stocked_drugs_made_there"]


def test_decisions_stay_inside_their_hospital_graph():
    memory.forget("childrens-hospital")
    memory.record_decision("childrens-hospital", "FUROSEMIDE", "TORSEMIDE", "test")
    assert memory.profile("childrens-hospital")["decisions"][0]["substitute"] == "TORSEMIDE"
    assert all(d["drug"] != "FUROSEMIDE" for d in memory.profile("surgical-hospital")["decisions"])
    memory.forget("childrens-hospital")


def test_graph_only_agent_names_the_first_drug_and_cites_intas(monkeypatch):
    monkeypatch.setattr(agent.settings, "anthropic_api_key", "")
    r = agent.ask("Cisplatin is short. Is carboplatin a safe switch?")
    assert r["mode"] in {"graph-only", "replay"}
    assert "3004011473" in r["answer"]
    assert [t["tool"] for t in r["trace"]][:2] == ["plants_for_drug", "substitute_trap"]


def test_betweenness_top_three_holds_on_both_engines():
    # FalkorDB 4.22 and 6.0 give different exact scores on the same 671 edges, but the same top three plants.
    top = {r["fei"] for r in queries.bridges(3)}
    assert top == {"3011895410", "3001833549", "1510690"}


def test_a_question_never_writes_to_memory(monkeypatch):
    monkeypatch.setattr(agent.settings, "anthropic_api_key", "")
    memory.forget("oncology-centre")
    agent.ask("Cisplatin is short. Is carboplatin a safe switch?", "oncology-centre")
    assert memory.profile("oncology-centre")["decisions"] == []
    agent.ask("We switched cisplatin to oxaliplatin because carboplatin shares the Intas plant.", "oncology-centre")
    assert memory.profile("oncology-centre")["decisions"][0]["substitute"] == "OXALIPLATIN"
    memory.forget("oncology-centre")


def test_everyday_drug_names_resolve_to_graph_names():
    out, _ = agent.run_tool("check_substitute", {"drug": "Midazolam", "substitute": ""})
    assert out["drug"] == "MIDAZOLAM HYDROCHLORIDE"
    assert out["same_class_with_no_shared_plant"], "plain names must not come back empty"


def test_safe_substitutes_count_only_flagged_plants():
    # FalkorDB 4.22 drops the second hop of a two-hop OPTIONAL MATCH; the query keeps it in WHERE instead.
    flagged = {r["substitute"]: r["flagged_plants"] for r in queries.safe("MIDAZOLAM HYDROCHLORIDE")}
    assert flagged["CLOBAZAM"] == 0
    assert flagged["CLONAZEPAM"] == 2  # both Intas sites make clonazepam
