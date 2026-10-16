from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_prefix="SS_", extra="ignore")

    falkordb_host: str = "localhost"
    falkordb_port: int = 6379
    graph_name: str = "supply"
    # replay: serve every HTTP call from fixtures/, fail if missing. live: fetch and record.
    mode: str = "replay"
    fixtures_dir: Path = ROOT / "fixtures"
    openfda_api_key: str = ""
    anthropic_api_key: str = ""
    http_concurrency: int = 6


settings = Settings()
