"""HTTP access with record/replay so the whole pipeline runs offline from fixtures/."""

import asyncio
import hashlib
from pathlib import Path

import httpx

from .config import settings


class MissingFixture(RuntimeError):
    pass


def _key(url: str, params: dict | None) -> str:
    raw = url + "?" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()) if k != "api_key")
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _path(url: str, params: dict | None) -> Path:
    return settings.fixtures_dir / "http" / _key(url, params)


class Fetcher:
    def __init__(self) -> None:
        self._sem = asyncio.Semaphore(settings.http_concurrency)
        self._pace_lock = asyncio.Lock()
        self._last = 0.0
        self._client = httpx.AsyncClient(
            timeout=60, follow_redirects=True, headers={"User-Agent": "single-source/0.1 (hackathon research)"}
        )

    async def __aenter__(self) -> "Fetcher":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._client.aclose()

    async def _pace(self, gap: float) -> None:
        # DailyMed throttles bursts; keep a steady gap between its requests.
        async with self._pace_lock:
            loop = asyncio.get_running_loop()
            wait = self._last + gap - loop.time()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = loop.time()

    async def get(self, url: str, params: dict | None = None, allow_404: bool = False) -> bytes | None:
        path = _path(url, params)
        if path.exists():
            data = path.read_bytes()
            return None if data == b"__404__" else data
        if settings.mode != "live":
            raise MissingFixture(f"No fixture for {url} {params or ''}. Run with SS_MODE=live once to record it.")
        for attempt in range(8):
            async with self._sem:
                if "dailymed" in url:
                    await self._pace(0.35)
                resp = await self._client.get(url, params=params)
            if resp.status_code not in (429, 500, 502, 503, 504):
                break
            wait = resp.headers.get("Retry-After", "")
            await asyncio.sleep(float(wait) if wait.isdigit() else min(5 * 2 ** attempt, 120))
        path.parent.mkdir(parents=True, exist_ok=True)
        # openFDA answers 404 for an empty search; record it so replay matches live.
        if resp.status_code == 404 and allow_404:
            path.write_bytes(b"__404__")
            return None
        resp.raise_for_status()
        path.write_bytes(resp.content)
        return resp.content
