"""Polite SEC EDGAR client with an on-disk cache.

SEC fair-access rules: <= 10 requests/second and a User-Agent that names you and gives
a contact email. Set it once:   export SEC_USER_AGENT="SJSU AI Research your.name@sjsu.edu"
Everything downloaded is cached under data/edgar_cache/, so re-runs are free and an
interrupted run resumes where it stopped.
"""
from __future__ import annotations

import gzip
import hashlib
import os
import threading
import time
from pathlib import Path

import httpx

CACHE_DIR = Path(os.environ.get("EDGAR_CACHE", Path(__file__).resolve().parent.parent / "data" / "edgar_cache"))
MAX_RPS = 8  # stay under SEC's 10/s ceiling


class SecClient:
    def __init__(self, user_agent: str | None = None, cache_dir: Path = CACHE_DIR, offline: bool = False):
        ua = user_agent or os.environ.get("SEC_USER_AGENT")
        if not ua and not offline:
            raise SystemExit('Set SEC_USER_AGENT, e.g. export SEC_USER_AGENT="SJSU AI Research your.name@sjsu.edu"')
        self.offline = offline
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._http = None if offline else httpx.Client(
            headers={"User-Agent": ua, "Accept-Encoding": "gzip, deflate"}, timeout=60.0, follow_redirects=True)
        self._lock = threading.Lock()
        self._last = 0.0

    def _cache_path(self, url: str) -> Path:
        h = hashlib.sha1(url.encode()).hexdigest()
        return self.cache_dir / h[:2] / f"{h}.gz"

    def _throttle(self) -> None:
        with self._lock:
            wait = self._last + 1.0 / MAX_RPS - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()

    def get(self, url: str, use_cache: bool = True) -> bytes:
        path = self._cache_path(url)
        if use_cache and path.exists():
            return gzip.decompress(path.read_bytes())
        if self.offline:
            raise FileNotFoundError(f"offline and not cached: {url}")
        for attempt in range(5):
            self._throttle()
            try:
                r = self._http.get(url)
            except httpx.TransportError:
                time.sleep(2 ** attempt)
                continue
            if r.status_code in (429, 503):  # SEC throttling -> back off
                time.sleep(10 * (attempt + 1))
                continue
            r.raise_for_status()
            if use_cache:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(gzip.compress(r.content))
            return r.content
        raise RuntimeError(f"gave up after retries: {url}")

    def download(self, url: str, dest: Path) -> Path:
        """Stream a large file (e.g. submissions.zip, several GB) straight to disk."""
        if dest.exists():
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        self._throttle()
        with self._http.stream("GET", url) as r:
            r.raise_for_status()
            with tmp.open("wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
        tmp.rename(dest)
        return dest
