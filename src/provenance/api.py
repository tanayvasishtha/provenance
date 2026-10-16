"""HTTP API and static page. Every graph answer carries the Cypher that produced it."""

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

import redis
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import agent, memory, queries
from .baseline import moiety, rag_substitutes
from .config import connect, settings
from .eval import SHORT_DRUGS

log = logging.getLogger("api")
SNAPSHOT_DATE = "16 Oct 2026"  # when `provenance build` last pulled the FDA records committed in data/
WEB = Path(__file__).resolve().parents[2] / "web"
def _choose_database() -> str:
    """Use the configured FalkorDB; fall back to the bundled one if it is unreachable. Load the snapshot if empty."""
    try:
        connect().connection.ping()
    except redis.exceptions.RedisError as e:
        if not settings.falkordb_fallback_host:
            raise
        log.warning("FalkorDB at %s unreachable (%s); using %s", settings.falkordb_host, e, settings.falkordb_fallback_host)
        settings.falkordb_host, settings.falkordb_port = settings.falkordb_fallback_host, 6379
        settings.falkordb_username = settings.falkordb_password = ""
    if settings.graph_name not in connect().list_graphs():
        from .baseline import embed_ingredients
        from .graph import load

        log.info("graph %s missing on %s; loading the committed snapshot", settings.graph_name, settings.falkordb_host)
        load()
        embed_ingredients()
    return settings.falkordb_host


@asynccontextmanager
async def lifespan(_app):
    log.info("FalkorDB: %s", _choose_database())
    agent.warm()  # open the pooled connection and read the drug list now, so the first visitor gets a fast answer
    memory.seed()  # demo hospital tenants; existing decisions are kept
    yield


app = FastAPI(title="Provenance", lifespan=lifespan)


@app.middleware("http")
async def no_stale_assets(request, call_next):
    # Small static page: always revalidate so a redeploy never shows an old script against a new API.
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


def _answer(cypher: str, rows: list[dict]) -> dict:
    return {"cypher": cypher.strip(), "rows": rows}


@app.get("/api/meta")
def meta() -> dict:
    counts = queries.run(
        "MATCH (f:Facility) WITH count(f) AS plants MATCH (p:Product) WITH plants, count(p) AS products "
        "MATCH (s:Shortage {status: 'Current'}) RETURN plants, products, count(s) AS current_shortages"
    )
    return {"snapshot": SNAPSHOT_DATE, **(counts[0] if counts else {})}


@app.get("/api/eval")
def eval_summary() -> dict:
    """The committed eval result, so the page can state the headline finding with its real numbers."""
    path = Path(__file__).resolve().parents[2] / "eval" / "results.json"
    return json.loads(path.read_text())["summary"] if path.exists() else {}


@app.get("/api/drugs")
def drugs() -> dict:
    short = [r["drug"] for r in queries.run(SHORT_DRUGS)]
    traced = queries.run("MATCH (i:Ingredient)<-[:CONTAINS]-(:Product)<-[:MAKES]-(:Facility) "
                         "WHERE i.vec IS NOT NULL RETURN DISTINCT i.name AS drug ORDER BY drug")
    return {"short": short, "other": [r["drug"] for r in traced if r["drug"] not in short]}


def _other_drug(rows: list[dict], drug: str) -> list[dict]:
    # A salt form of the same drug (LIDOCAINE vs LIDOCAINE HCL) is not a substitute.
    return [r for r in rows if moiety(r["substitute"]) != moiety(drug.upper())]


@app.get("/api/trap")
def trap(drug: str) -> dict:
    # Both queries leave at once: the database can be a continent away from the web server.
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows, plants = pool.submit(queries.trap, drug), pool.submit(queries.drug_plants, drug)
    return {**_answer(queries.TRAP, _other_drug(rows.result(), drug)),
            "plants": plants.result(), "plants_cypher": queries.DRUG_PLANTS.strip()}


@app.get("/api/safe")
def safe(drug: str) -> dict:
    return _answer(queries.SAFE, _other_drug(queries.safe(drug), drug))


@app.get("/api/rag")
def rag(drug: str) -> dict:
    return {"method": "nearest label text (bge-small, FalkorDB vector index)", "rows": rag_substitutes(drug, k=3)}


@app.get("/api/plant/{fei}")
def plant(fei: str) -> dict:
    rows = queries.plant_down(fei)
    if not rows:
        raise HTTPException(404, f"No plant with FEI {fei} in the graph.")
    return _answer(queries.PLANT_DOWN, rows)


@app.get("/api/plants")
def plants() -> dict:
    """Plants for the picker: every plant under an FDA import alert, then the most exposed ones."""
    flagged = queries.run("MATCH (f:Facility)-[:FLAGGED]->(a:Action) RETURN f.name AS plant, f.fei AS fei, f.country AS country, "
                          "collect(a.date) AS import_alert_dates ORDER BY plant")
    top = [r for r in queries.chokepoints(60) if r["fei"] not in {f["fei"] for f in flagged}]
    return {"flagged": flagged, "exposed": top}


@app.get("/api/bridges")
def bridges(limit: int = 15) -> dict:
    return _answer(queries.BRIDGES, queries.bridges(limit))


@app.get("/api/chokepoints")
def chokepoints(limit: int = 15) -> dict:
    return _answer(queries.CHOKEPOINTS, queries.chokepoints(limit))


class Ask(BaseModel):
    question: str = Field(min_length=3, max_length=600)
    hospital: str | None = None


@app.post("/api/ask")
def ask(body: Ask) -> dict:
    if body.hospital and body.hospital not in memory.DEMO_HOSPITALS:
        raise HTTPException(404, f"Unknown hospital {body.hospital}")
    return agent.ask(body.question, body.hospital)


@app.get("/api/hospitals")
def hospitals() -> list[dict]:
    return memory.hospitals()


@app.get("/api/hospital/{slug}")
def hospital(slug: str) -> dict:
    try:
        return memory.profile(slug)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e


@app.post("/api/hospital/{slug}/forget")
def forget(slug: str) -> dict:
    memory.forget(slug)
    return memory.profile(slug)


@app.get("/api/event/{fei}")
def event(fei: str) -> dict:
    return memory.plant_event(fei)


if WEB.exists():
    app.mount("/static", StaticFiles(directory=WEB), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(WEB / "index.html")
