import argparse
import logging

import redis


def main() -> None:
    parser = argparse.ArgumentParser(prog="provenance", description="Trace drugs and their substitutes to the plants that make them.")
    parser.add_argument("command", choices=["build", "load", "eval", "serve"],
                        help="build: pull FDA data (needs PV_MODE=live). load: snapshot into FalkorDB plus vectors. "
                             "eval: vector pick vs graph. serve: web app on :8000.")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    try:
        if args.command == "build":
            import asyncio

            from .build import build
            asyncio.run(build())
        elif args.command == "load":
            from .baseline import embed_ingredients
            from .graph import load
            load()
            embed_ingredients()
        elif args.command == "eval":
            from .eval import evaluate
            evaluate()
        else:
            import uvicorn
            uvicorn.run("provenance.api:app", port=args.port)
    except redis.exceptions.ConnectionError as e:
        raise SystemExit(
            f"FalkorDB is not reachable ({e}). Start it with scripts/falkordb-wsl.sh "
            "or `docker run -p 6379:6379 falkordb/falkordb:v6.0.1`."
        ) from None
