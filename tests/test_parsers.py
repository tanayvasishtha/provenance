import asyncio
from pathlib import Path

from single_source import graph, sources

DATA = Path(__file__).parent / "data"


class OneFile:
    def __init__(self, body: bytes):
        self.body = body

    async def get(self, url, params=None, allow_404=False):
        return self.body


def test_label_names_intas_as_maker_of_accord_cisplatin():
    est = asyncio.run(sources.establishments(OneFile((DATA / "accord_cisplatin_spl.xml").read_bytes()), "b2b029f5"))
    intas = [e for e in est if e["duns"] == "915837971"]
    assert intas and intas[0]["name"] == "Intas Pharmaceuticals Limited"
    assert {e["op"] for e in intas} >= {"manufacture"}
    assert "16729-288" in intas[0]["ndcs"]


def test_import_alert_matches_the_right_intas_site():
    registry = [
        {"fei": "3004011473", "duns": "915837971", "name": "Intas Pharmaceuticals Limited", "country": "IND",
         "address": "Plot No. 5 to 14, Pharmez, Near Village Matoda, Sarkhej-Bavla Highway, Taluka Sanand, Ahmedabad"},
        {"fei": "3003157498", "duns": "725927649", "name": "Intas Pharmaceuticals Limited", "country": "IND",
         "address": "Plot No. 457/458-Matoda / Plot No.191/218P-Chacharwadi, Sarkhej-Bavla Highway, Taluka-Sanand, Matoda"},
    ]
    alerts = [
        {"name": "Intas Pharmaceuticals Limited", "country": "INDIA", "date": "06/01/2023",
         "address": "Pharmez Plot No 5-14, Near Village Matoda; Sarkhej-Bavla Highway No 8-A, Sanand, Gujarat INDIA"},
        {"name": "Intas Pharmaceuticals Limited", "country": "INDIA", "date": "11/14/2023",
         "address": "Plot No. 457/458-Matoda / Plot No.191/218P-Chacharwadi, Sarkhej-Bavla Highway, Taluka-Sanand, Matoda"},
    ]
    out, unmatched = graph._match_alerts(alerts, registry, {"915837971", "725927649"})
    assert unmatched == 0
    assert {(o["duns"], o["date"]) for o in out} == {("915837971", "06/01/2023"), ("725927649", "11/14/2023")}


def test_name_normalisation_ignores_legal_suffixes():
    assert graph._norm("Intas Pharmaceuticals Limited") == graph._norm("INTAS PHARMACEUTICALS LTD.")
