FROM python:3.11-slim-trixie

# redis-server 8 from Debian, plus the official FalkorDB v6.0.1 module (pinned).
RUN apt-get update \
 && apt-get install -y --no-install-recommends redis-server libgomp1 curl ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /opt/falkordb \
 && curl -fsSLo /opt/falkordb/falkordb.so https://github.com/FalkorDB/FalkorDB/releases/download/v6.0.1/falkordb-x64.so \
 && echo "155b2fedd99422adfa7f2541cd0d9becabc05839cf5319ed8d604341cdae445d  /opt/falkordb/falkordb.so" | sha256sum -c - \
 && chmod +x /opt/falkordb/falkordb.so

COPY --from=ghcr.io/astral-sh/uv:0.11.16 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY web ./web
COPY data ./data
COPY eval ./eval
COPY deploy/start.sh ./start.sh
RUN uv sync --frozen --no-dev && chmod +x start.sh
ENV PATH="/app/.venv/bin:$PATH" PV_MODE=replay HF_HUB_DISABLE_TELEMETRY=1
# Warm the embedding model at build time so first boot works offline.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')"

EXPOSE 8000
CMD ["./start.sh"]
