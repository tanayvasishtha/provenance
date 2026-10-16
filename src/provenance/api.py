"""HTTP API and static page. Every graph answer carries the Cypher that produced it."""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import queries
from .baseline import moiety, rag_substitutes
from .config import settings
from .eval import SHORT_DRUGS

WEB = Path(__file__).resolve().parents[2] / "web"
app = FastAPI(title="Provenance")


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
    return {"mode": settings.mode, **(counts[0] if counts else {})}


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
    return {**_answer(queries.TRAP, _other_drug(queries.trap(drug), drug)),
            "plants": queries.drug_plants(drug), "plants_cypher": queries.DRUG_PLANTS.strip()}


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


@app.get("/api/chokepoints")
def chokepoints(limit: int = 15) -> dict:
    return _answer(queries.CHOKEPOINTS, queries.chokepoints(limit))


if WEB.exists():
    app.mount("/static", StaticFiles(directory=WEB), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(WEB / "index.html")
