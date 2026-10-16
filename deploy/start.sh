#!/usr/bin/env sh
# One container: FalkorDB (redis-server + the FalkorDB module) in the background, the web app in front.
set -e
mkdir -p /data
redis-server --port 6379 --dir /data --loadmodule /opt/falkordb/falkordb.so --daemonize yes --save 300 1
for i in $(seq 1 30); do redis-cli ping >/dev/null 2>&1 && break; sleep 0.5; done
# First boot on an empty volume: load the committed FDA snapshot and the label-text vectors.
if [ "$(redis-cli EXISTS supply)" = "0" ]; then
  provenance load
fi
exec uvicorn provenance.api:app --host 0.0.0.0 --port "${PORT:-8000}"
