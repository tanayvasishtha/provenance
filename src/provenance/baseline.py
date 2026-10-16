"""The plain-RAG baseline: pick the substitute whose label text is most similar, using FalkorDB's vector index."""

import asyncio
import json
import logging

import redis
from lxml import etree

from .build import SNAPSHOT
from .fetch import Fetcher, MissingFixture
from .graph import graph
from .sources import DAILYMED_SPL, HL7

log = logging.getLogger("baseline")
INDICATIONS = "34067-9"  # LOINC section code for "Indications and Usage"
MODEL = "BAAI/bge-small-en-v1.5"
DIM = 384
# Committed so replay mode needs no raw label XML; rebuilt from fixtures when missing.
LABEL_TEXT = SNAPSHOT.parent / "label_text.json"


def moiety(name: str) -> str:
    """First word of the substance name, so salt forms of one drug (LIDOCAINE HCL vs anhydrous) count as the same drug."""
    return name.split()[0]


def _indications(xml: bytes) -> str:
    root = etree.fromstring(xml)
    for sec in root.iterfind(".//v:section", HL7):
        code = sec.find("v:code", HL7)
        if code is not None and code.get("code") == INDICATIONS:
            return " ".join(" ".join(sec.itertext()).split())[:1500]
    return ""


async def _texts(snap: dict) -> dict[str, str]:
    setids: dict[str, list[str]] = {}
    for p in snap["products"]:
        if p["setid"] and len(p["substances"]) == 1:
            setids.setdefault(p["substances"][0], []).append(p["setid"])
    out = {}
    async with Fetcher() as f:
        for ing, ids in setids.items():
            for sid in ids[:5]:
                try:
                    body = await f.get(DAILYMED_SPL.format(setid=sid), allow_404=True)
                except MissingFixture:
                    continue
                text = _indications(body) if body else ""
                if text:
                    out[ing] = f"{ing.title()}. {text}"
                    break
    return out


def embed_ingredients() -> int:
    from fastembed import TextEmbedding

    snap = json.loads(SNAPSHOT.read_text())
    if LABEL_TEXT.exists():
        texts = json.loads(LABEL_TEXT.read_text())
    else:
        texts = asyncio.run(_texts(snap))
        LABEL_TEXT.write_text(json.dumps(texts, indent=0, sort_keys=True))
    names = sorted(texts)
    vecs = list(TextEmbedding(MODEL).embed([texts[n] for n in names]))
    g = graph()
    try:
        g.query(f"CREATE VECTOR INDEX FOR (i:Ingredient) ON (i.vec) OPTIONS {{dimension:{DIM}, similarityFunction:'cosine'}}")
    except redis.exceptions.ResponseError:
        pass  # index already exists
    g.query("UNWIND $rows AS r MATCH (i:Ingredient {name: r.n}) SET i.vec = vecf32(r.v), i.label_text = r.t",
            {"rows": [{"n": n, "v": [float(x) for x in v], "t": texts[n]} for n, v in zip(names, vecs, strict=True)]})
    log.info("embedded %d of %d ingredients", len(names), len({s for p in snap["products"] for s in p["substances"]}))
    return len(names)


NEAREST = """
MATCH (i:Ingredient {name: $drug})
CALL db.idx.vector.queryNodes('Ingredient', 'vec', $k, i.vec) YIELD node, score
WHERE node <> i
RETURN node.name AS substitute, score
"""


def rag_substitutes(drug: str, k: int = 3) -> list[dict]:
    """Most similar label text, other drugs only (different moiety), best first."""
    res = graph().ro_query(NEAREST, {"drug": drug.upper(), "k": k + 8})
    rows = [{"substitute": r[0], "score": round(float(r[1]), 4)} for r in res.result_set]
    return [r for r in rows if moiety(r["substitute"]) != moiety(drug.upper())][:k]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")
    embed_ingredients()


if __name__ == "__main__":
    main()
