"""Readers for the public FDA sources. Each returns plain dicts; graph.py turns them into nodes and edges."""

import asyncio
import csv
import io
import json
import re
import zipfile
from collections import Counter

import httpx
from lxml import etree, html

from .fetch import Fetcher

OPENFDA = "https://api.fda.gov/drug"
DAILYMED_SPL = "https://dailymed.nlm.nih.gov/dailymed/services/v2/spls/{setid}.xml"
DECRS_ZIP = "https://www.accessdata.fda.gov/cder/drls_reg.zip"
IMPORT_ALERT_6640 = "https://www.accessdata.fda.gov/cms_ia/importalert_189.html"

HL7 = {"v": "urn:hl7-org:v3"}
DUNS_ROOT = "1.3.6.1.4.1.519.1"
# FDA SPL operation codes we treat as "this plant makes or releases the product".
MAKING_OPS = {"manufacture", "api manufacture", "pack", "repack", "relabel", "label", "sterilize", "analysis"}


async def _openfda_all(f: Fetcher, endpoint: str, search: str, page: int = 1000) -> list[dict]:
    rows: list[dict] = []
    skip = 0
    while True:
        body = await f.get(f"{OPENFDA}/{endpoint}.json", {"search": search, "limit": page, "skip": skip}, allow_404=True)
        if body is None:
            break
        data = json.loads(body)
        rows += data["results"]
        skip += page
        if skip >= data["meta"]["results"]["total"]:
            break
    return rows


async def shortages(f: Fetcher) -> list[dict]:
    """Every row on the FDA drug shortage list (openFDA drug/shortages)."""
    return await _openfda_all(f, "shortages", "_exists_:generic_name")


def scope_substances(rows: list[dict], top_n: int, always: set[str]) -> list[str]:
    """Substances with the most current shortage rows, plus the ones we always want (platinum agents)."""
    counts: Counter[str] = Counter()
    for r in rows:
        if r.get("status") != "Current":
            continue
        for s in r.get("openfda", {}).get("substance_name", []):
            counts[s] += 1
    picked = [s for s, _ in counts.most_common(top_n)]
    return sorted(set(picked) | always)


async def products_for(f: Fetcher, substance: str) -> list[dict]:
    """Finished human Rx products containing the substance (openFDA drug/ndc)."""
    q = f'active_ingredients.name:"{substance}" AND product_type:"HUMAN PRESCRIPTION DRUG"'
    return await _openfda_all(f, "ndc", q, page=100)


async def class_members(f: Fetcher, epc: str) -> list[str]:
    """Substances in the same established pharmacologic class, i.e. the candidate substitutes."""
    body = await f.get(
        f"{OPENFDA}/ndc.json",
        {"search": f'pharm_class:"{epc}" AND product_type:"HUMAN PRESCRIPTION DRUG"',
         "count": "active_ingredients.name.exact", "limit": 50},
        allow_404=True,
    )
    return [] if body is None else [r["term"] for r in json.loads(body)["results"]]


async def establishments(f: Fetcher, setid: str) -> list[dict]:
    """Plants named in a DailyMed label: DUNS, name, operation, and the product NDCs each operation covers."""
    try:
        body = await f.get(DAILYMED_SPL.format(setid=setid), allow_404=True)
    except httpx.HTTPStatusError:
        # Kept out of the snapshot and counted; a rerun picks it up because nothing was recorded.
        return [{"missing": setid}]
    if body is None:
        return []
    root = etree.fromstring(body)
    out = []
    for ent in root.iterfind(".//v:assignedEntity/v:assignedOrganization/v:assignedEntity", HL7):
        org = ent.find("v:assignedOrganization", HL7)
        if org is None:
            continue
        duns = next((i.get("extension") for i in org.iterfind("v:id", HL7) if i.get("root") == DUNS_ROOT), None)
        name = (org.findtext("v:name", default="", namespaces=HL7) or "").strip()
        for perf in ent.iterfind("v:performance/v:actDefinition", HL7):
            op = (perf.find("v:code", HL7).get("displayName") or "").lower()
            ndcs = [c.get("code") for c in perf.iterfind(".//v:manufacturedMaterialKind/v:code", HL7)]
            if duns and op in MAKING_OPS:
                out.append({"duns": duns.lstrip("0"), "name": name, "op": op, "ndcs": ndcs, "setid": setid})
    return out


async def decrs(f: Fetcher) -> list[dict]:
    """FDA Drug Establishments Current Registration Site: FEI, DUNS, firm, address, country, operations."""
    body = await f.get(DECRS_ZIP)
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        text = z.read("drls_reg.txt").decode("latin-1")
    out = []
    for row in csv.DictReader(io.StringIO(text), delimiter="\t"):
        row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
        addr = row.get("ADDRESS", "")
        m = re.search(r"\(([A-Z]{3})\)\s*$", addr)
        out.append({
            "fei": row["FEI_NUMBER"].lstrip("0"),
            "duns": row["DUNS_NUMBER"].lstrip("0"),
            "name": row["FIRM_NAME"],
            "address": addr,
            "country": m.group(1) if m else "",
            "operations": row.get("OPERATIONS", ""),
        })
    return out


async def import_alert_6640(f: Fetcher) -> list[dict]:
    """Firms on FDA Import Alert 66-40 (drug GMP), with the date each was added."""
    doc = html.fromstring(await f.get(IMPORT_ALERT_6640))
    t = [x.strip() for x in doc.itertext() if x.strip()]
    firms = []
    # Firm-level rows read "Date Published : 06/01/2023" (space before the colon) between name and address;
    # product-level rows use "Date Published: " and are skipped.
    for k, tok in enumerate(t):
        m = re.match(r"^Date Published : (\d{2}/\d{2}/\d{4})$", tok)
        if m and 0 < k < len(t) - 1:
            addr = re.sub(r"\s+", " ", t[k + 1])
            firms.append({"name": t[k - 1], "address": addr, "country": addr.split()[-1].upper(), "date": m.group(1)})
    return firms


async def gather_limited(coros):
    return await asyncio.gather(*coros)
