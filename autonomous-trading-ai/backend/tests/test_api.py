import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    db = tmp_path_factory.mktemp("api") / "api.db"
    os.environ.update(DATABASE_URL=f"sqlite+aiosqlite:///{db}", DATA_MODE="sandbox", AGENT_AUTOSTART="false",
                      ADMIN_PASSWORD="test-password-123", TELEGRAM_BOT_TOKEN="")
    from app.config import get_settings
    get_settings.cache_clear()
    import app.db as dbm
    dbm.init_engine(f"sqlite+aiosqlite:///{db}")
    from app.main import app
    with TestClient(app) as c:
        yield c


def token(c):
    return {"authorization": "Bearer " + c.post("/api/auth/login", json={"username": "admin", "password": "test-password-123"}).json()["token"]}


def test_auth_required(client):
    assert client.get("/api/dashboard").status_code == 401
    assert client.post("/api/auth/login", json={"username": "admin", "password": "bad"}).status_code == 401
    assert client.get("/api/health").json()["mode"] == "PAPER"


def test_end_to_end_and_controls(client):
    h = token(client)
    assert client.post("/api/agent/tick", headers=h).status_code == 200
    d = client.get("/api/dashboard", headers=h).json()
    assert d["data_label"] == "synthetic" and d["mode"] == "PAPER" and d["portfolio"]["initial_capital"] == 52.0
    assert client.get("/api/scanner?category=gainers&timeframe=5m", headers=h).json()["category"] == "gainers"
    r = client.get("/api/scanner?category=trending&timeframe=4h", headers=h).json()
    assert r["timeframe_is_approximation"] is True
    assert client.get("/api/traders", headers=h).status_code == 200
    assert client.post("/api/traders", headers=h, json={"address": "not-an-address"}).status_code == 422
    # kill switch blocks resume until disabled; everything audited
    assert client.post("/api/risk/kill", headers=h, json={"active": True}).json()["kill_switch"] is True
    assert client.post("/api/agent/resume", headers=h).status_code == 409
    client.post("/api/risk/kill", headers=h, json={"active": False})
    assert client.post("/api/agent/resume", headers=h).status_code == 200
    assert client.put("/api/risk", headers=h, json={"overrides": {"bogus": 1}}).status_code == 422
    assert client.put("/api/copy", headers=h, json={"copy_params": {"multiplier": 50}}).status_code == 422
    assert client.put("/api/copy", headers=h, json={"copy_params": {"multiplier": 0.1}}).json()["copy_params"]["multiplier"] == 0.1
    actions = [a["action"] for a in client.get("/api/audit", headers=h).json()]
    assert "kill_switch" in actions and "update_copy" in actions
    live = client.get("/api/live/status", headers=h).json()
    assert live["allowed"] is False and live["wired_to_agent"] is False
    assert client.post("/api/live/arm", headers=h, json={"password": "test-password-123", "phrase": "x"}).status_code == 403
    assert client.post("/api/live/arm", headers=h, json={"password": "test-password-123",
                                                          "phrase": live["arm_phrase"]}).status_code == 409  # env disabled


def test_backtest_endpoint(client):
    h = token(client)
    r = client.post("/api/backtest", headers=h, json={"source": "synthetic", "days": 4, "strategies": ["B", "F"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body["results"]) == {"B", "F"} and body["results"]["B"]["data_label"] == "synthetic"
    rec = client.post("/api/backtest", headers=h, json={"source": "recorded", "days": 5, "strategies": ["B"]})
    # in sandbox mode, data recorded by the agent is still synthetic and must be labelled so
    assert rec.status_code == 409 or rec.json()["results"]["B"]["data_label"] == "synthetic_recorded"
