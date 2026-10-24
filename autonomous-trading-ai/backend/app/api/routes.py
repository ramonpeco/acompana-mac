"""REST API consumed by the web panel."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import audit, create_token, current_user, require_admin, verify_password
from app.backtest.data import SyntheticDataset, load_recorded
from app.backtest.engine import STRATEGIES, BTConfig, run_all, walk_forward
from app.chains.sandbox import SandboxWorld
from app.config import get_settings
from app.db import get_session
from app.execution.live import ARM_PHRASE, guard_status
from app.models import (
    AgentEvent,
    AIAnalysis,
    AuditLog,
    BacktestRun,
    EquitySnapshot,
    FollowSession,
    Order,
    Portfolio,
    Position,
    StrategyVersion,
    Token,
    TraderScore,
    User,
    Wallet,
    WalletTrade,
)
from app.risk.profiles import CRITICAL_KEYS, PROFILES, CopyParams, RiskLimits, SwitchRules
from app.runtime import kv_get, kv_set, load_runtime
from app.sim.execution import ExecConfig
from app.sim.portfolio import open_positions, valuation
from app.state import get_state
from app.traders.metrics import compute_metrics, compute_round_trips
from app.traders.service import valid_solana_address

router = APIRouter(prefix="/api")
Auth = Depends(current_user)
Admin = Depends(require_admin)


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


# ------------------------------------------------------------------ auth
class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/auth/login")
async def login(body: LoginIn, session: AsyncSession = Depends(get_session)):
    u = (await session.execute(select(User).where(User.username == body.username))).scalar_one_or_none()
    if u is None or not verify_password(body.password, u.password_hash):
        await audit(session, body.username[:64], "login_failed")
        await session.commit()
        await asyncio.sleep(1.0)  # slow down brute force
        raise HTTPException(401, "credenciales incorrectas")
    await audit(session, u.username, "login")
    await session.commit()
    return {"token": create_token(u.username, u.role), "username": u.username, "role": u.role}


@router.get("/auth/me")
async def me(user: dict = Auth):
    return user


# ------------------------------------------------------------------ dashboard
async def _main(session: AsyncSession) -> Portfolio | None:
    return (await session.execute(select(Portfolio).where(Portfolio.name == "main"))).scalar_one_or_none()


@router.get("/dashboard")
async def dashboard(session: AsyncSession = Depends(get_session), user: dict = Auth):
    st = get_state()
    main = await _main(session)
    rt = await load_runtime(session)
    out: dict[str, Any] = {"agent": st.agent.status, "data_label": st.adapter.data_label, "mode": "PAPER",
                           "connectors": st.adapter.health(), "paused": rt.paused, "kill_switch": rt.kill_switch,
                           "profile": rt.profile, "telegram": bool(st.telegram and st.telegram.healthy),
                           "ai_provider": st.ai.provider.name, "heartbeat": await kv_get(session, "agent_heartbeat")}
    if main is None:
        return {**out, "portfolio": None}
    v = await valuation(session, main)
    closed = (await session.execute(select(Position).where(Position.portfolio_id == main.id, Position.status == "closed"))).scalars().all()
    wins = sum(1 for p in closed if p.realized_pnl > 0)
    follow = (await session.execute(select(FollowSession).where(FollowSession.portfolio_id == main.id, FollowSession.ended_at.is_(None)))).scalars().first()
    snaps = (await session.execute(select(EquitySnapshot).where(EquitySnapshot.portfolio_id == main.id)
                                   .order_by(desc(EquitySnapshot.ts)).limit(1500))).scalars().all()
    out["portfolio"] = {
        "equity": v.equity, "initial_capital": main.initial_capital, "cash": v.cash, "exposure": v.exposure,
        "pnl": v.equity - main.initial_capital, "return_pct": (v.equity / main.initial_capital - 1) * 100,
        "unrealized": v.unrealized, "realized": sum(p.realized_pnl for p in closed), "drawdown_pct": v.drawdown_pct,
        "peak_equity": main.peak_equity, "daily_pnl_pct": v.daily_pnl_pct, "fees": main.fees_paid, "slippage": main.slippage_paid,
        "open_positions": len(await open_positions(session, main.id)), "closed_positions": len(closed),
        "win_rate": wins / len(closed) if closed else None,
    }
    out["follow"] = _follow_dict(follow) if follow else None
    out["equity_curve"] = [{"ts": iso(s.ts), "equity": s.equity, "drawdown_pct": s.drawdown_pct} for s in reversed(snaps)]
    return out


def _follow_dict(f: FollowSession) -> dict[str, Any]:
    sel = dict(f.selection or {})
    return {"id": f.id, "wallet": f.wallet, "started_at": iso(f.started_at), "ended_at": iso(f.ended_at),
            "end_reason": f.end_reason, "score_at_start": f.score_at_start, "closed_trades": f.closed_trades,
            "wins": f.wins, "losses": f.losses, "consecutive_losses": f.consecutive_losses,
            "realized_pnl": f.realized_pnl, "selection": sel, "shadow_portfolio_id": f.shadow_portfolio_id,
            "strategy_version": f.strategy_version}


@router.get("/positions")
async def positions(status: str = "open", portfolio: str = "main", limit: int = 100,
                    session: AsyncSession = Depends(get_session), user: dict = Auth):
    p = (await session.execute(select(Portfolio).where(Portfolio.name == portfolio))).scalar_one_or_none()
    if p is None:
        return []
    rows = (await session.execute(select(Position).where(Position.portfolio_id == p.id, Position.status == status)
                                  .order_by(desc(Position.opened_at)).limit(limit))).scalars().all()
    return [{"id": x.id, "mint": x.mint, "symbol": x.symbol, "qty": x.qty, "cost_basis": x.cost_basis,
             "value": x.qty * (x.last_price or 0), "last_price": x.last_price, "realized_pnl": x.realized_pnl,
             "invested": x.invested_total, "proceeds": x.proceeds_total, "fees": x.fees, "source_wallet": x.source_wallet,
             "opened_at": iso(x.opened_at), "closed_at": iso(x.closed_at), "close_reason": x.close_reason,
             "unrealized": x.qty * (x.last_price or 0) - x.cost_basis if x.status == "open" else None} for x in rows]


@router.get("/orders")
async def orders(portfolio: str = "main", limit: int = 100, session: AsyncSession = Depends(get_session), user: dict = Auth):
    p = (await session.execute(select(Portfolio).where(Portfolio.name == portfolio))).scalar_one_or_none()
    if p is None:
        return []
    rows = (await session.execute(select(Order).where(Order.portfolio_id == p.id).order_by(desc(Order.id)).limit(limit))).scalars().all()
    return [{"id": o.id, "side": o.side, "mint": o.mint, "status": o.status, "reason": o.reason, "reject_reason": o.reject_reason,
             "requested_usd": o.requested_usd, "filled_usd": o.filled_usd, "filled_qty": o.filled_qty,
             "reference_price": o.reference_price, "exec_price": o.exec_price, "trader_price": o.trader_price,
             "fee_usd": o.fee_usd, "slippage_bps": o.slippage_bps, "latency_ms": o.latency_ms, "quote_source": o.quote_source,
             "trader_signature": o.trader_signature, "trader_block_time": iso(o.trader_block_time),
             "detected_at": iso(o.detected_at), "executed_at": iso(o.executed_at),
             "signal_delay_s": (o.detected_at - o.trader_block_time).total_seconds() if o.detected_at and o.trader_block_time else None}
            for o in rows]


@router.post("/positions/{pid}/close")
async def close_position(pid: int, session: AsyncSession = Depends(get_session), user: dict = Admin):
    pos = await session.get(Position, pid)
    if pos is None or pos.status != "open":
        raise HTTPException(404, "posición no abierta")
    p = await session.get(Portfolio, pos.portfolio_id)
    rt = await load_runtime(session)
    res = await get_state().agent.engine.close_position(session, p, pos, rt, "manual_close")
    await audit(session, user["sub"], "close_position", position_id=pid, result=res.action)
    await session.commit()
    return {"action": res.action, "detail": res.detail}


# ------------------------------------------------------------------ scanner
@router.get("/scanner")
async def scanner(category: str = "trending", timeframe: str = "1h", min_liquidity: float | None = None,
                  min_volume: float | None = None, min_mcap: float | None = None, max_mcap: float | None = None,
                  chain: str = "solana", limit: int = Query(100, le=500),
                  session: AsyncSession = Depends(get_session), user: dict = Auth):
    return await get_state().agent.scanner.query(session, category, timeframe, min_liquidity=min_liquidity,
                                                 min_volume=min_volume, min_mcap=min_mcap, max_mcap=max_mcap,
                                                 chain=chain, limit=limit)


@router.post("/scanner/refresh")
async def scanner_refresh(session: AsyncSession = Depends(get_session), user: dict = Admin):
    stats = await get_state().agent.scanner.refresh(session)
    await session.commit()
    return stats


@router.get("/tokens/{address}")
async def token_detail(address: str, session: AsyncSession = Depends(get_session), user: dict = Auth):
    t = (await session.execute(select(Token).where(Token.address == address))).scalar_one_or_none()
    if t is None:
        raise HTTPException(404, "token desconocido")
    buyers = (await session.execute(select(WalletTrade.wallet, func.count()).where(
        WalletTrade.mint == address, WalletTrade.kind == "BUY").group_by(WalletTrade.wallet).limit(50))).all()
    return {"address": t.address, "symbol": t.symbol, "name": t.name, "market": t.market, "risk": t.risk,
            "categories": t.categories, "pool_created_at": iso(t.pool_created_at),
            "tracked_wallet_buyers": [{"wallet": w, "buys": n} for w, n in buyers]}


@router.post("/tokens/{address}/assess")
async def token_assess(address: str, session: AsyncSession = Depends(get_session), user: dict = Auth):
    st = get_state()
    t = (await session.execute(select(Token).where(Token.address == address))).scalar_one_or_none()
    if t is None:
        ms = await st.adapter.markets([address])
        if address not in ms:
            raise HTTPException(404, "sin datos de mercado")
        t = await st.agent.scanner._upsert(session, ms[address], None)
    risk = await st.agent.scanner.assess(session, t)
    await session.commit()
    return risk


# ------------------------------------------------------------------ traders
@router.get("/traders")
async def traders(status: str | None = None, limit: int = 200, session: AsyncSession = Depends(get_session), user: dict = Auth):
    q = select(Wallet)
    if status:
        q = q.where(Wallet.status == status)
    ws = (await session.execute(q)).scalars().all()
    ws.sort(key=lambda w: (w.score is not None, w.score or 0), reverse=True)
    out = []
    for i, w in enumerate(ws[:limit]):
        m = w.metrics or {}
        out.append({"rank": i + 1, "address": w.address, "label": w.label, "origin": w.origin, "status": w.status,
                    "score": w.score, "excluded_reasons": w.excluded_reasons, "roi": m.get("roi"), "win_rate": m.get("win_rate"),
                    "realized_pnl": m.get("realized_pnl"), "n_closed": m.get("n_closed"), "profit_factor": m.get("profit_factor"),
                    "max_drawdown_pct": m.get("max_drawdown_pct"), "median_hold_hours": m.get("median_hold_hours"),
                    "trades_per_day": m.get("trades_per_day"), "data_quality": m.get("data_quality"),
                    "verified": m.get("verified"), "last_trade_at": iso(w.last_trade_at), "discovered_via": w.discovered_via})
    return out


class WalletIn(BaseModel):
    address: str
    label: str | None = Field(default=None, max_length=64)


@router.post("/traders")
async def add_trader(body: WalletIn, session: AsyncSession = Depends(get_session), user: dict = Admin):
    if not valid_solana_address(body.address):
        raise HTTPException(422, "dirección Solana inválida")
    svc = get_state().agent.traders
    w = await svc.add_wallet(session, body.address, origin="manual", label=body.label)
    try:
        await svc.sync_wallet(session, w, backfill_pages=4)
    except Exception as exc:  # keep the wallet even if the first sync fails
        await session.commit()
        return {"address": w.address, "synced": False, "error": str(exc)}
    await svc.rank(session)
    await audit(session, user["sub"], "add_wallet", address=body.address)
    await session.commit()
    return {"address": w.address, "synced": True, "score": w.score, "status": w.status}


@router.get("/traders/{address}")
async def trader_detail(address: str, session: AsyncSession = Depends(get_session), user: dict = Auth):
    w = (await session.execute(select(Wallet).where(Wallet.address == address))).scalar_one_or_none()
    if w is None:
        raise HTTPException(404, "wallet desconocida")
    rows = (await session.execute(select(WalletTrade).where(WalletTrade.wallet == address).order_by(WalletTrade.block_time))).scalars().all()
    trips, extra = compute_round_trips(rows)
    eq, curve = 0.0, []
    for r in sorted(trips, key=lambda r: r.closed_at):
        eq += r.pnl
        curve.append({"ts": iso(r.closed_at), "cum_pnl": eq})
    hist = (await session.execute(select(TraderScore).where(TraderScore.wallet == address).order_by(desc(TraderScore.computed_at)).limit(50))).scalars().all()
    syms = {t.address: t.symbol for t in (await session.execute(select(Token).where(Token.address.in_({r.mint for r in rows})))).scalars().all()} if rows else {}
    return {
        "address": w.address, "label": w.label, "status": w.status, "score": w.score, "origin": w.origin,
        "excluded_reasons": w.excluded_reasons, "metrics": w.metrics, "history_complete": w.history_complete,
        "first_tx_at": iso(w.first_tx_at), "last_trade_at": iso(w.last_trade_at), "discovered_via": w.discovered_via,
        "equity_curve_estimated": curve,
        "round_trips": [{"mint": r.mint, "symbol": syms.get(r.mint), "opened_at": iso(r.opened_at), "closed_at": iso(r.closed_at),
                         "invested": r.invested, "proceeds": r.proceeds, "pnl": r.pnl, "ret": r.ret, "hold_hours": r.hold_hours}
                        for r in trips[-100:]],
        "recent_trades": [{"signature": r.signature, "block_time": iso(r.block_time), "kind": r.kind, "mint": r.mint,
                           "symbol": syms.get(r.mint), "token_amount": r.token_amount, "quote_usd": r.quote_usd,
                           "usd_is_estimate": r.usd_is_estimate, "source": r.source} for r in rows[-100:][::-1]],
        "score_history": [{"ts": iso(h.computed_at), "score": h.score, "eligible": h.eligible} for h in reversed(hist)],
        "unknown_basis_tokens": len(extra["tainted"]),
        "note": "Curva estimada solo con operaciones cerradas de coste conocido; no es un resultado verificado salvo que 'verified' sea true.",
    }


# ------------------------------------------------------------------ copy trading
@router.get("/copy")
async def copy_state(session: AsyncSession = Depends(get_session), user: dict = Auth):
    main = await _main(session)
    rt = await load_runtime(session)
    sessions = (await session.execute(select(FollowSession).order_by(desc(FollowSession.id)).limit(100))).scalars().all()
    current = next((s for s in sessions if s.ended_at is None and main and s.portfolio_id == main.id), None)
    return {"current": _follow_dict(current) if current else None, "copy_params": rt.copy.to_dict(),
            "switch_rules": rt.switch.to_dict(), "history": [_follow_dict(s) for s in sessions],
            "shadow_comparison": await get_state().agent.shadow_comparison(session)}


class CopyParamsIn(BaseModel):
    copy_params: dict[str, Any] | None = None
    switch_rules: dict[str, Any] | None = None


@router.put("/copy")
async def update_copy(body: CopyParamsIn, session: AsyncSession = Depends(get_session), user: dict = Admin):
    if body.copy_params is not None:
        cp = CopyParams.from_dict({**CopyParams().to_dict(), **(await kv_get(session, "copy_params", {}) or {}), **body.copy_params})
        if not (0 < cp.multiplier <= 2):
            raise HTTPException(422, "multiplicador fuera de rango (0, 2]")
        await kv_set(session, "copy_params", cp.to_dict(), user["sub"])
    if body.switch_rules is not None:
        sr = SwitchRules.from_dict({**SwitchRules().to_dict(), **(await kv_get(session, "switch_rules", {}) or {}), **body.switch_rules})
        if sr.selection not in ("score", "top_pnl", "ai_supervised"):
            raise HTTPException(422, "modo de selección inválido")
        await kv_set(session, "switch_rules", sr.to_dict(), user["sub"])
    await audit(session, user["sub"], "update_copy", body=body.model_dump())
    await session.commit()
    return await copy_state(session, user)


@router.post("/copy/unfollow")
async def manual_unfollow(session: AsyncSession = Depends(get_session), user: dict = Admin):
    main = await _main(session)
    ag = get_state().agent
    f = await ag.active_follow(session, main.id) if main else None
    if f is None:
        raise HTTPException(404, "no hay trader seguido")
    await ag.unfollow(session, main, f, await load_runtime(session), "manual", f"cambio manual por {user['sub']}")
    await audit(session, user["sub"], "manual_unfollow", wallet=f.wallet)
    await session.commit()
    return {"ok": True}


# ------------------------------------------------------------------ AI
@router.get("/ai")
async def ai_state(limit: int = 50, session: AsyncSession = Depends(get_session), user: dict = Auth):
    st = get_state()
    an = (await session.execute(select(AIAnalysis).order_by(desc(AIAnalysis.id)).limit(limit))).scalars().all()
    decisions = (await session.execute(select(AgentEvent).where(AgentEvent.kind.in_(
        ("trader_selected", "trader_switched", "trader_unfollowed", "ai_veto_all", "risk_alert", "daily_report")))
        .order_by(desc(AgentEvent.id)).limit(limit))).scalars().all()
    props = (await session.execute(select(StrategyVersion).order_by(desc(StrategyVersion.id)).limit(50))).scalars().all()
    return {
        "provider": st.ai.provider.name, "model": st.ai.provider.model, "available": st.ai.available,
        "analyses": [{"id": a.id, "ts": iso(a.ts), "kind": a.kind, "provider": a.provider, "model": a.model, "ok": a.ok,
                      "output": a.output, "error": a.error} for a in an],
        "decisions": [{"id": e.id, "ts": iso(e.ts), "kind": e.kind, "level": e.level, "message": e.message, "data": e.data} for e in decisions],
        "proposals": [{"id": p.id, "name": p.name, "status": p.status, "params": p.params, "proposed_by": p.proposed_by,
                       "rationale": p.rationale, "oos_results": p.oos_results, "approved_by": p.approved_by,
                       "created_at": iso(p.created_at)} for p in props],
        "guardrails": ["La IA no ejecuta órdenes", "Solo elige entre candidatos validados", "No modifica límites críticos",
                       "Propuestas versionadas: validación fuera de muestra + aprobación admin"],
    }


def _apply_changes(rt_dicts: dict[str, dict], changes: list[dict]) -> dict[str, dict]:
    out = {k: dict(v) for k, v in rt_dicts.items()}
    for c in changes:
        if c["section"] == "risk_overrides" and c["key"] in CRITICAL_KEYS:
            continue
        out.setdefault(c["section"], {})[c["key"]] = c["value"]
    return out


@router.post("/strategies/{sid}/validate")
async def validate_strategy(sid: int, days: int = 14, session: AsyncSession = Depends(get_session), user: dict = Admin):
    sv = await session.get(StrategyVersion, sid)
    if sv is None:
        raise HTTPException(404)
    rt = await load_runtime(session)
    end = datetime.now(UTC)
    data = await load_recorded(session, end - timedelta(days=days), end)
    if len(data.trades) < 50:
        raise HTTPException(409, f"datos registrados insuficientes ({len(data.trades)} operaciones) para validar fuera de muestra")
    dicts = _apply_changes({"switch_rules": rt.switch.to_dict(), "copy_params": rt.copy.to_dict(), "risk_overrides": rt.limits.to_dict()},
                           sv.params.get("changes", []))
    base_cfg = BTConfig(initial_capital=get_settings().initial_capital_usd, limits=rt.limits, copy=rt.copy, switch=rt.switch)
    new_cfg = BTConfig(initial_capital=base_cfg.initial_capital, limits=RiskLimits.from_dict(dicts["risk_overrides"]),
                       copy=CopyParams.from_dict(dicts["copy_params"]), switch=SwitchRules.from_dict(dicts["switch_rules"]))
    wf_base = await asyncio.to_thread(walk_forward, data, base_cfg)
    wf_new = await asyncio.to_thread(walk_forward, data, new_cfg)
    sv.oos_results = {"baseline": wf_base, "proposal": wf_new, "validated_at": end.isoformat(), "days": days}
    await audit(session, user["sub"], "validate_strategy", id=sid)
    await session.commit()
    return sv.oos_results


@router.post("/strategies/{sid}/{action}")
async def decide_strategy(sid: int, action: str, session: AsyncSession = Depends(get_session), user: dict = Admin):
    sv = await session.get(StrategyVersion, sid)
    if sv is None or action not in ("approve", "reject", "activate"):
        raise HTTPException(404)
    if action == "reject":
        sv.status = "rejected"
    elif action == "approve":
        if not sv.oos_results:
            raise HTTPException(409, "primero valida la propuesta fuera de muestra")
        sv.status, sv.approved_by = "approved", user["sub"]
    elif action == "activate":
        if sv.status != "approved":
            raise HTTPException(409, "solo se activan propuestas aprobadas")
        for c in sv.params.get("changes", []):
            if c["section"] == "risk_overrides" and c["key"] in CRITICAL_KEYS:
                continue
            key = c["section"]
            cur = await kv_get(session, key, {}) or {}
            cur[c["key"]] = c["value"]
            await kv_set(session, key, cur, user["sub"])
        await kv_set(session, "strategy_version", sv.name, user["sub"])
        sv.status, sv.activated_at = "active", datetime.now(UTC)
    await audit(session, user["sub"], f"strategy_{action}", id=sid)
    await session.commit()
    return {"id": sid, "status": sv.status}


# ------------------------------------------------------------------ risk & agent control
@router.get("/risk")
async def risk_state(session: AsyncSession = Depends(get_session), user: dict = Auth):
    rt = await load_runtime(session)
    main = await _main(session)
    v = await valuation(session, main) if main else None
    return {"profile": rt.profile, "limits": rt.limits.to_dict(), "profiles": {k: p.to_dict() for k, p in PROFILES.items()},
            "critical_keys": sorted(CRITICAL_KEYS), "kill_switch": rt.kill_switch, "paused": rt.paused,
            "current": {"drawdown_pct": v.drawdown_pct, "daily_pnl_pct": v.daily_pnl_pct, "exposure": v.exposure,
                        "equity": v.equity} if v else None}


class RiskIn(BaseModel):
    profile: str | None = None
    overrides: dict[str, float] | None = None


@router.put("/risk")
async def update_risk(body: RiskIn, session: AsyncSession = Depends(get_session), user: dict = Admin):
    if body.profile:
        if body.profile not in PROFILES:
            raise HTTPException(422, "perfil desconocido")
        await kv_set(session, "risk_profile", body.profile, user["sub"])
    if body.overrides is not None:
        valid = set(RiskLimits.__dataclass_fields__)
        bad = [k for k in body.overrides if k not in valid]
        if bad:
            raise HTTPException(422, f"claves desconocidas: {bad}")
        if any(v < 0 for v in body.overrides.values()):
            raise HTTPException(422, "valores negativos no permitidos")
        await kv_set(session, "risk_overrides", body.overrides, user["sub"])
    await audit(session, user["sub"], "update_risk", body=body.model_dump(),
                critical=sorted(set(body.overrides or {}) & CRITICAL_KEYS))
    await session.commit()
    return await risk_state(session, user)


class Toggle(BaseModel):
    active: bool


@router.post("/risk/kill")
async def kill_switch(body: Toggle, session: AsyncSession = Depends(get_session), user: dict = Admin):
    await kv_set(session, "kill_switch", body.active, user["sub"])
    if body.active:
        await kv_set(session, "agent_paused", True, user["sub"])
    await audit(session, user["sub"], "kill_switch", active=body.active)
    from app.events import bus
    await bus.emit(session, "alert", "kill_switch", "Parada de emergencia ACTIVADA" if body.active else "Parada de emergencia desactivada")
    await session.commit()
    return {"kill_switch": body.active}


@router.post("/agent/{action}")
async def agent_control(action: str, session: AsyncSession = Depends(get_session), user: dict = Admin):
    ag = get_state().agent
    from app.events import bus
    if action == "pause":
        await kv_set(session, "agent_paused", True, user["sub"])
        await bus.emit(session, "warn", "agent_paused", f"Agente pausado por {user['sub']}")
    elif action == "resume":
        if (await load_runtime(session)).kill_switch:
            raise HTTPException(409, "desactiva antes la parada de emergencia")
        await kv_set(session, "agent_paused", False, user["sub"])
        await bus.emit(session, "info", "agent_resumed", f"Agente reanudado por {user['sub']}")
    elif action == "start":
        ag.start()
    elif action == "stop":
        await ag.stop()
    elif action == "tick":
        await ag.tick()
    else:
        raise HTTPException(404)
    await audit(session, user["sub"], f"agent_{action}")
    await session.commit()
    return {"status": ag.status}


# ------------------------------------------------------------------ backtesting
class BacktestIn(BaseModel):
    source: str = "synthetic"  # synthetic | recorded
    regime: str = "neutral"
    days: float = Field(10, ge=2, le=90)
    strategies: list[str] = Field(default_factory=lambda: list(STRATEGIES))
    profile: str = "EQUILIBRADO"
    initial_capital: float = Field(52.0, gt=0)
    latency_ms: int = 2500
    poll_seconds: float = 15
    dex_fee_bps: float = 25
    seed: int = 7
    walk_forward: bool = False


@router.post("/backtest")
async def run_backtest(body: BacktestIn, session: AsyncSession = Depends(get_session), user: dict = Auth):
    if body.profile not in PROFILES or any(k not in STRATEGIES for k in body.strategies):
        raise HTTPException(422, "perfil o estrategia inválidos")
    cfg = BTConfig(initial_capital=body.initial_capital, profile=body.profile, poll_seconds=body.poll_seconds, seed=body.seed,
                   exec=ExecConfig(latency_ms=body.latency_ms, dex_fee_bps=body.dex_fee_bps))
    if body.source == "recorded":
        end = datetime.now(UTC)
        data = await load_recorded(session, end - timedelta(days=body.days), end)
        if get_state().adapter.data_label == "synthetic":
            data.label = "synthetic_recorded"  # recorded by the agent, but from the sandbox world
        if len(data.trades) < 20:
            raise HTTPException(409, f"Solo hay {len(data.trades)} operaciones registradas en el periodo. El agente "
                                     "debe acumular datos reales antes de un backtest con datos registrados.")
    else:
        if body.regime not in ("neutral", "favorable", "unfavorable"):
            raise HTTPException(422, "régimen inválido")
        world = await asyncio.to_thread(SandboxWorld, seed=body.seed, history_days=0, horizon_days=body.days,
                                        start=datetime(2026, 1, 1, tzinfo=UTC), regime=body.regime, n_traders=40)
        data = SyntheticDataset(world)
    res = await asyncio.to_thread(run_all, data, cfg, body.strategies)
    if body.walk_forward:
        res["walk_forward"] = await asyncio.to_thread(walk_forward, data, cfg)
    run = BacktestRun(params=body.model_dump(), results=res, data_source=data.label)
    session.add(run)
    await audit(session, user["sub"], "backtest", params=body.model_dump())
    await session.commit()
    return {"id": run.id, **res}


@router.get("/backtests")
async def list_backtests(session: AsyncSession = Depends(get_session), user: dict = Auth):
    rows = (await session.execute(select(BacktestRun).order_by(desc(BacktestRun.id)).limit(50))).scalars().all()
    return [{"id": r.id, "created_at": iso(r.created_at), "params": r.params, "data_source": r.data_source,
             "summary": [{k: x.get(k) for k in ("strategy", "net_return_pct", "max_drawdown_pct", "sharpe")}
                         for x in (r.results or {}).get("comparison", {}).get("table", [])]} for r in rows]


@router.get("/backtests/{rid}")
async def get_backtest(rid: int, session: AsyncSession = Depends(get_session), user: dict = Auth):
    r = await session.get(BacktestRun, rid)
    if r is None:
        raise HTTPException(404)
    return {"id": r.id, "params": r.params, "data_source": r.data_source, **(r.results or {})}


# ------------------------------------------------------------------ events, audit, live
@router.get("/events")
async def events(limit: int = 100, kind: str | None = None, session: AsyncSession = Depends(get_session), user: dict = Auth):
    q = select(AgentEvent).order_by(desc(AgentEvent.id)).limit(min(limit, 500))
    if kind:
        q = q.where(AgentEvent.kind == kind)
    return [{"id": e.id, "ts": iso(e.ts), "level": e.level, "kind": e.kind, "message": e.message, "data": e.data}
            for e in (await session.execute(q)).scalars().all()]


@router.get("/audit")
async def audit_log(limit: int = 200, session: AsyncSession = Depends(get_session), user: dict = Admin):
    rows = (await session.execute(select(AuditLog).order_by(desc(AuditLog.id)).limit(limit))).scalars().all()
    return [{"id": a.id, "ts": iso(a.ts), "actor": a.actor, "action": a.action, "details": a.details} for a in rows]


@router.get("/live/status")
async def live_status(session: AsyncSession = Depends(get_session), user: dict = Auth):
    st = await guard_status(session, get_settings())
    return {"allowed": st.allowed, "checks": st.checks, "reasons": st.reasons, "wired_to_agent": False,
            "arm_phrase": ARM_PHRASE}


class ArmIn(BaseModel):
    password: str
    phrase: str


@router.post("/live/arm")
async def live_arm(body: ArmIn, session: AsyncSession = Depends(get_session), user: dict = Admin):
    s = get_settings()
    u = (await session.execute(select(User).where(User.username == user["sub"]))).scalar_one()
    if not verify_password(body.password, u.password_hash) or body.phrase.strip() != ARM_PHRASE:
        await audit(session, user["sub"], "live_arm_denied")
        await session.commit()
        raise HTTPException(403, "confirmación inválida")
    if not s.live_trading_enabled:
        await audit(session, user["sub"], "live_arm_denied", reason="env disabled")
        await session.commit()
        raise HTTPException(409, "LIVE_TRADING_ENABLED=false en el servidor")
    await kv_set(session, "live_armed_at", datetime.now(UTC).isoformat(), user["sub"])
    await audit(session, user["sub"], "live_armed")
    await session.commit()
    return await live_status(session, user)


@router.post("/live/disarm")
async def live_disarm(session: AsyncSession = Depends(get_session), user: dict = Admin):
    await kv_set(session, "live_armed_at", None, user["sub"])
    await audit(session, user["sub"], "live_disarmed")
    await session.commit()
    return {"ok": True}


@router.get("/stats/trader-metrics/{address}")
async def recompute(address: str, session: AsyncSession = Depends(get_session), user: dict = Auth):
    rows = (await session.execute(select(WalletTrade).where(WalletTrade.wallet == address))).scalars().all()
    return compute_metrics(rows, now=datetime.now(UTC)).to_dict()
