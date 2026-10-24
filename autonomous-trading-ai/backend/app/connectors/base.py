"""Shared HTTP connector infrastructure.

Every external data source goes through :class:`HttpConnector`, which provides:

* token-bucket rate limiting (per connector, configurable requests/second)
* retries with exponential backoff + jitter on 429 / 5xx / network errors,
  honouring ``Retry-After`` when present
* a small TTL cache (avoids burning rate limit on identical requests)
* a circuit breaker that stops hammering a failing service
* health statistics exposed in the dashboard (status, latency, last error)
* source attribution: every payload is returned wrapped in :class:`Sourced`
  with the connector name and fetch time so staleness can be checked downstream.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Generic, TypeVar

import httpx

log = logging.getLogger(__name__)
T = TypeVar("T")


class ConnectorError(Exception):
    def __init__(self, connector: str, message: str, status: int | None = None):
        super().__init__(f"[{connector}] {message}")
        self.connector = connector
        self.status = status


class CircuitOpenError(ConnectorError):
    pass


@dataclass
class Sourced(Generic[T]):
    """A value tagged with where and when it came from."""

    value: T
    source: str
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    cached: bool = False

    def age_seconds(self, now: datetime | None = None) -> float:
        now = now or datetime.now(UTC)
        return (now - self.fetched_at).total_seconds()

    def is_stale(self, max_age_s: float, now: datetime | None = None) -> bool:
        return self.age_seconds(now) > max_age_s


class TokenBucket:
    def __init__(self, rate_per_s: float, burst: int | None = None):
        self.rate = max(rate_per_s, 0.01)
        self.capacity = burst or max(1, int(rate_per_s))
        self.tokens = float(self.capacity)
        self.updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await asyncio.sleep((1 - self.tokens) / self.rate)


@dataclass
class ConnectorHealth:
    name: str
    requests: int = 0
    errors: int = 0
    consecutive_failures: int = 0
    last_success: datetime | None = None
    last_error: str | None = None
    last_error_at: datetime | None = None
    avg_latency_ms: float = 0.0
    circuit_open_until: float = 0.0
    rate_limited: int = 0

    @property
    def status(self) -> str:
        if self.circuit_open_until > time.monotonic():
            return "down"
        if self.requests == 0:
            return "idle"
        if self.consecutive_failures >= 1:
            return "degraded"
        return "ok"

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "requests": self.requests,
            "errors": self.errors,
            "rate_limited": self.rate_limited,
            "consecutive_failures": self.consecutive_failures,
            "last_success": self.last_success.isoformat() if self.last_success else None,
            "last_error": self.last_error,
            "last_error_at": self.last_error_at.isoformat() if self.last_error_at else None,
            "avg_latency_ms": round(self.avg_latency_ms, 1),
        }


class HttpConnector:
    name = "http"
    retry_statuses = {408, 425, 429, 500, 502, 503, 504}

    def __init__(
        self,
        base_url: str,
        *,
        rate_per_s: float = 1.0,
        burst: int | None = None,
        timeout_s: float = 15.0,
        max_retries: int = 4,
        backoff_base_s: float = 1.0,
        cache_ttl_s: float = 10.0,
        headers: dict[str, str] | None = None,
        circuit_threshold: int = 6,
        circuit_cooldown_s: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.bucket = TokenBucket(rate_per_s, burst)
        self.max_retries = max_retries
        self.backoff_base_s = backoff_base_s
        self.cache_ttl_s = cache_ttl_s
        self.circuit_threshold = circuit_threshold
        self.circuit_cooldown_s = circuit_cooldown_s
        self.health = ConnectorHealth(self.name)
        self._cache: dict[str, tuple[float, Any, datetime]] = {}
        self._client = client or httpx.AsyncClient(
            timeout=timeout_s, headers={"accept": "application/json", **(headers or {})}
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _cache_key(self, method: str, path: str, params: Any, body: Any) -> str:
        raw = json.dumps([method, path, params, body], sort_keys=True, default=str)
        return hashlib.sha1(raw.encode()).hexdigest()

    def _record_success(self, latency_ms: float) -> None:
        h = self.health
        h.requests += 1
        h.consecutive_failures = 0
        h.last_success = datetime.now(UTC)
        h.avg_latency_ms = latency_ms if h.avg_latency_ms == 0 else 0.8 * h.avg_latency_ms + 0.2 * latency_ms

    def _record_failure(self, msg: str) -> None:
        h = self.health
        h.requests += 1
        h.errors += 1
        h.consecutive_failures += 1
        h.last_error = msg[:300]
        h.last_error_at = datetime.now(UTC)
        if h.consecutive_failures >= self.circuit_threshold:
            h.circuit_open_until = time.monotonic() + self.circuit_cooldown_s
            log.warning("circuit opened for %s after %d failures", self.name, h.consecutive_failures)

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        headers: dict[str, str] | None = None,
        cache_ttl_s: float | None = None,
    ) -> Sourced[Any]:
        ttl = self.cache_ttl_s if cache_ttl_s is None else cache_ttl_s
        key = self._cache_key(method, path, params, json_body)
        now = time.monotonic()
        if ttl > 0 and key in self._cache:
            exp, val, fetched = self._cache[key]
            if exp > now:
                return Sourced(val, self.name, fetched, cached=True)

        if self.health.circuit_open_until > now:
            raise CircuitOpenError(self.name, "circuit open (service failing, cooling down)")

        url = path if path.startswith("http") else f"{self.base_url}{path}"
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            await self.bucket.acquire()
            t0 = time.monotonic()
            try:
                resp = await self._client.request(method, url, params=params, json=json_body, headers=headers)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
                self._record_failure(f"network: {exc!r}")
                await self._sleep_backoff(attempt, None)
                continue
            latency = (time.monotonic() - t0) * 1000
            if resp.status_code in self.retry_statuses:
                if resp.status_code == 429:
                    self.health.rate_limited += 1
                last_exc = ConnectorError(self.name, f"HTTP {resp.status_code}", resp.status_code)
                self._record_failure(f"HTTP {resp.status_code}: {resp.text[:200]}")
                await self._sleep_backoff(attempt, resp.headers.get("retry-after"))
                continue
            if resp.status_code >= 400:
                self._record_failure(f"HTTP {resp.status_code}: {resp.text[:200]}")
                raise ConnectorError(self.name, f"HTTP {resp.status_code}: {resp.text[:200]}", resp.status_code)
            try:
                data = resp.json()
            except ValueError as exc:
                self._record_failure("invalid JSON")
                raise ConnectorError(self.name, "invalid JSON response") from exc
            self._record_success(latency)
            fetched = datetime.now(UTC)
            if ttl > 0:
                self._cache[key] = (time.monotonic() + ttl, data, fetched)
                if len(self._cache) > 2000:
                    self._evict()
            return Sourced(data, self.name, fetched)
        raise ConnectorError(self.name, f"exhausted retries: {last_exc!r}")

    async def get(self, path: str, **kw: Any) -> Sourced[Any]:
        return await self.request("GET", path, **kw)

    async def post(self, path: str, **kw: Any) -> Sourced[Any]:
        return await self.request("POST", path, **kw)

    async def _sleep_backoff(self, attempt: int, retry_after: str | None) -> None:
        if attempt >= self.max_retries:
            return
        delay = self.backoff_base_s * (2**attempt)
        if retry_after:
            try:
                delay = max(delay, float(retry_after))
            except ValueError:
                pass
        delay = min(delay, 30.0) * (0.75 + random.random() * 0.5)
        await asyncio.sleep(delay)

    def _evict(self) -> None:
        now = time.monotonic()
        for k in [k for k, (exp, _, _) in self._cache.items() if exp <= now]:
            self._cache.pop(k, None)
        while len(self._cache) > 1500:
            self._cache.pop(next(iter(self._cache)))


def fnum(v: Any) -> float | None:
    """Parse a number that APIs may send as str/float/None."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
