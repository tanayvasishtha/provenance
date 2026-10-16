"""Pull the scoped FDA data and write one normalised snapshot (data/snapshot.json) for the graph loader."""

import asyncio
import json
import logging

from . import sources
from .config import ROOT, settings
from .fetch import Fetcher

log = logging.getLogger("build")

ALWAYS = {"CISPLATIN", "CARBOPLATIN", "OXALIPLATIN"}
TOP_N = 20
MAX_CLAPV_MEMBERS = 8
SNAPSHOT = ROOT / "data" / "snapshot.json"


async def build() -> dict:
    async with Fetcher() as f:
        short_rows = await sources.shortages(f)
        scope = sources.scope_substances(short_rows, TOP_N, ALWAYS)
        log.info("shortage rows %d, scoped substances %d", len(short_rows), len(scope))

        # Single-ingredient products only: a combination product is not a like-for-like substitute,
        # and its partner ingredient would pull unrelated classes into scope.
        prod_lists = [_single(pl) for pl in await asyncio.gather(*(sources.products_for(f, s) for s in scope))]
        classes = sorted({c for pl in prod_lists for p in pl for c in p.get("pharm_class", []) if c.endswith("[EPC]")})
        members = await asyncio.gather(*(sources.class_members(f, c) for c in classes))
        substitutes = sorted({m for ms in members for m in ms[:MAX_CLAPV_MEMBERS]} - set(scope))
        log.info("EPC classes %d, substitute substances %d", len(classes), len(substitutes))

        prod_lists += [_single(pl) for pl in await asyncio.gather(*(sources.products_for(f, s) for s in substitutes))]
        products: dict[str, dict] = {}
        for pl in prod_lists:
            for p in pl:
                products[p["product_ndc"]] = {
                    "ndc": p["product_ndc"],
                    "name": p.get("brand_name") or p.get("generic_name", ""),
                    "generic": p.get("generic_name", ""),
                    "labeler": p.get("labeler_name", ""),
                    "route": ", ".join(p.get("route", [])),
                    "form": p.get("dosage_form", ""),
                    "substances": [i["name"] for i in p.get("active_ingredients", [])],
                    "classes": [c for c in p.get("pharm_class", []) if c.endswith("[EPC]")],
                    "setid": (p.get("openfda", {}).get("spl_set_id") or [None])[0],
                }
        setids = sorted({p["setid"] for p in products.values() if p["setid"]})
        log.info("products %d, labels %d", len(products), len(setids))

        est_lists = await asyncio.gather(*(sources.establishments(f, s) for s in setids))
        makes = [e for el in est_lists for e in el if "missing" not in e]
        missing = [e["missing"] for el in est_lists for e in el if "missing" in e]
        log.info("labels missing after retries: %d", len(missing))
        registry = await sources.decrs(f)
        alerts = await sources.import_alert_6640(f)

    snap = {
        "shortages": [_short(r) for r in short_rows],
        "scope": scope,
        "substitutes": substitutes,
        "products": list(products.values()),
        "makes": makes,
        "registry": registry,
        "import_alert_6640": alerts,
        "labels_missing": missing,
    }
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(json.dumps(snap))
    log.info("makes %d, registry %d, import alert firms %d", len(makes), len(registry), len(alerts))
    return snap


def _single(products: list[dict]) -> list[dict]:
    return [p for p in products if len(p.get("active_ingredients", [])) == 1]


def _short(r: dict) -> dict:
    o = r.get("openfda", {})
    return {
        "package_ndc": r.get("package_ndc", ""),
        "product_ndcs": o.get("product_ndc", []),
        "generic": r.get("generic_name", ""),
        "substances": o.get("substance_name", []),
        "company": r.get("company_name", ""),
        "status": r.get("status", ""),
        "availability": r.get("availability", ""),
        "reason": r.get("shortage_reason", ""),
        "posted": r.get("initial_posting_date", ""),
        "category": r.get("therapeutic_category", []),
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")
    log.info("mode %s", settings.mode)
    asyncio.run(build())


if __name__ == "__main__":
    main()
