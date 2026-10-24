import json

import pytest
from sqlalchemy import select

from app.ai.provider import LLMProvider
from app.ai.supervisor import AISupervisor
from app.config import Settings
from app.db import create_all, init_engine, session_scope
from app.execution.live import decrypt_secret, encrypt_secret, guard_status
from app.models import StrategyVersion


@pytest.fixture
async def db(tmp_path):
    init_engine(f"sqlite+aiosqlite:///{tmp_path}/s.db")
    await create_all()


class FakeLLM(LLMProvider):
    name, model = "fake", "fake-1"

    def __init__(self, out):
        self.out = out

    async def complete_json(self, system, prompt, schema, max_tokens=4000):
        assert "SIMULADO" in system
        return json.loads(json.dumps(self.out))


async def test_ai_cannot_pick_unlisted_wallet(db):
    ai = AISupervisor(FakeLLM({"reviews": [{"wallet": "EVIL", "verdict": "approve", "confidence": 1, "anomalies": [], "reasoning": ""},
                                           {"wallet": "A", "verdict": "veto", "confidence": .9, "anomalies": ["x"], "reasoning": ""}],
                               "preferred_wallet": "EVIL", "summary": "s"}))
    async with session_scope() as s:
        out = await ai.review_candidates(s, [{"wallet": "A", "score": 70}])
    assert out["preferred_wallet"] is None and [r["wallet"] for r in out["reviews"]] == ["A"]


async def test_ai_critical_proposals_rejected_and_others_need_approval(db):
    ai = AISupervisor(FakeLLM({"patterns": ["p"], "summary": "s", "proposals": [
        {"section": "risk_overrides", "key": "max_drawdown_pct", "value": 90, "rationale": "more risk"},
        {"section": "switch_rules", "key": "consecutive_losses", "value": 3, "rationale": "less churn"}]}))
    async with session_scope() as s:
        out = await ai.analyse_failures(s, {"losing_positions": []})
    assert out["rejected"][0]["key"] == "max_drawdown_pct"
    async with session_scope() as s:
        sv = (await s.execute(select(StrategyVersion))).scalar_one()
    assert sv.status == "proposed" and sv.params["changes"] == [out["proposals"][1]]


async def test_ai_unavailable_falls_back(db):
    from app.ai.provider import NullProvider
    async with session_scope() as s:
        out = await AISupervisor(NullProvider()).review_candidates(s, [{"wallet": "A"}, {"wallet": "B"}])
    assert out["source"] == "fallback" and out["preferred_wallet"] == "A"


async def test_live_disabled_by_default(db, tmp_path):
    s = Settings(live_keystore_path=str(tmp_path / "none.enc"))
    async with session_scope() as ses:
        st = await guard_status(ses, s)
    assert not st.allowed and not st.checks["env_enabled"] and not st.checks["admin_armed"]


def test_keystore_roundtrip_and_wrong_pass():
    blob = encrypt_secret(b"\x01" * 64, "correct horse battery")
    assert decrypt_secret(blob, "correct horse battery") == b"\x01" * 64
    from cryptography.exceptions import InvalidTag
    with pytest.raises(InvalidTag):
        decrypt_secret(blob, "wrong passphrase!!")
    with pytest.raises(ValueError):
        encrypt_secret(b"x", "short")
