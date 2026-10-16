#!/usr/bin/env sh
# One container: FalkorDB (redis-server + the FalkorDB module) in the background, the web app in front.
set -e
mkdir -p /data
redis-server --port 6379 --dir /data --loadmodule /opt/falkordb/falkordb.so --daemonize yes --save 300 1
for i in $(seq 1 30); do redis-cli ping >/dev/null 2>&1 && break; sleep 0.5; done
# The app connects to PV_FALKORDB_HOST (FalkorDB Cloud in production), falls back to this local FalkorDB when that
# is unreachable, and loads the committed snapshot into whichever one is empty.
export PV_FALKORDB_FALLBACK_HOST="${PV_FALKORDB_FALLBACK_HOST:-localhost}"
exec uvicorn provenance.api:app --host 0.0.0.0 --port "${PORT:-8000}"
