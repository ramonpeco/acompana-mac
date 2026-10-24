import httpx
import pytest

from app.connectors.base import CircuitOpenError, ConnectorError, HttpConnector
from app.connectors.solana_rpc import SolanaRPC


def make(handler, **kw):
    c = HttpConnector("https://example.test", rate_per_s=1000, backoff_base_s=0.001,
                      client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), **kw)
    return c


async def test_retry_on_429_then_success_and_source_tag():
    calls = {"n": 0}

    def h(req):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, headers={"retry-after": "0"})
        return httpx.Response(200, json={"ok": 1})

    c = make(h, cache_ttl_s=0)
    res = await c.get("/x")
    assert res.value == {"ok": 1} and calls["n"] == 3 and res.source == "http" and not res.cached
    assert c.health.rate_limited == 2


async def test_cache_dedupes_identical_requests():
    calls = {"n": 0}

    def h(req):
        calls["n"] += 1
        return httpx.Response(200, json=[calls["n"]])

    c = make(h, cache_ttl_s=60)
    a, b = await c.get("/y", params={"q": 1}), await c.get("/y", params={"q": 1})
    assert a.value == b.value == [1] and b.cached and calls["n"] == 1


async def test_4xx_is_not_retried_and_circuit_opens():
    calls = {"n": 0}

    def h(req):
        calls["n"] += 1
        return httpx.Response(404, text="nope")

    c = make(h, cache_ttl_s=0, circuit_threshold=2)
    with pytest.raises(ConnectorError):
        await c.get("/z")
    assert calls["n"] == 1
    with pytest.raises(ConnectorError):
        await c.get("/z")
    with pytest.raises(CircuitOpenError):
        await c.get("/z")
    assert c.health.status == "down"


async def test_staleness():
    c = make(lambda r: httpx.Response(200, json={}), cache_ttl_s=0)
    res = await c.get("/s")
    assert not res.is_stale(60)
    from datetime import timedelta
    assert res.is_stale(60, now=res.fetched_at + timedelta(seconds=61))


async def test_rpc_error_surfaces():
    def h(req):
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32005, "message": "rate"}})

    rpc = SolanaRPC("https://rpc.test", rate_per_s=1000, client=httpx.AsyncClient(transport=httpx.MockTransport(h)))
    with pytest.raises(ConnectorError, match="-32005"):
        await rpc.get_slot()
