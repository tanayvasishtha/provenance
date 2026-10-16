from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_prefix="PV_", extra="ignore")

    falkordb_host: str = "localhost"
    falkordb_port: int = 6379
    # FalkorDB Cloud gives a username and password; a local FalkorDB needs neither.
    falkordb_username: str = ""
    falkordb_password: str = ""
    # If the main FalkorDB is unreachable at startup (a free cloud instance can be stopped or deleted), use this one.
    falkordb_fallback_host: str = ""
    graph_name: str = "supply"
    # replay: serve every HTTP call from fixtures/, fail if missing. live: fetch and record.
    mode: str = "replay"
    fixtures_dir: Path = ROOT / "fixtures"
    openfda_api_key: str = ""
    anthropic_api_key: str = ""
    http_concurrency: int = 6


settings = Settings()


_CLIENTS: dict = {}


def connect():
    """One pooled client per target. A fresh connection to FalkorDB Cloud costs about 400 ms; a query on an open one, 30 ms."""
    from falkordb import FalkorDB

    key = (settings.falkordb_host, settings.falkordb_port, settings.falkordb_username, settings.falkordb_password)
    if key not in _CLIENTS:
        _CLIENTS[key] = FalkorDB(host=key[0], port=key[1], username=key[2] or None, password=key[3] or None)
    return _CLIENTS[key]
