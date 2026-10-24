"""Event-driven backtester for trader-selection strategies.

No look-ahead: rankings at time t use only trades with ``block_time < t``; our
copies execute at ``trade_time + detection_delay`` at the latest price observed
*before* that moment, moved adversely for the unobserved gap, then through the
same execution simulator, sizing and risk engine as the live paper engine.
"""

from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.backtest.data import BTTrade, Dataset, SyntheticDataset
from app.chains.types import WSOL_MINT
from app.copytrade.logic import SessionStats, copy_buy_size, evaluate_switch, sell_fraction
from app.risk.engine import EntryRequest, check_entry, exit_triggers
from app.risk.profiles import PROFILES, CopyParams, RiskLimits, SwitchRules
from app.sim.execution import ExecConfig, MarketContext, simulate
from app.traders.metrics import compute_metrics
from app.traders.scoring import ScoringConfig, score_trader


@dataclass
class StrategySpec:
    key: str
    name: str
    selection: str  # top_pnl | score | ai_proxy | none
    consecutive_losses: int = 0
    reselect_on_rank: bool = False  # switch whenever the ranking leader changes (A) or beats by margin (D)
    reselect_margin: float = 0.0


STRATEGIES: dict[str, StrategySpec] = {
    "A": StrategySpec("A", "Siempre el trader nº1 (PnL)", "top_pnl", 0, reselect_on_rank=True),
    "B": StrategySpec("B", "Cambio tras 2 pérdidas consecutivas", "score", 2),
    "C": StrategySpec("C", "Cambio tras 3 pérdidas consecutivas", "score", 3),
    "D": StrategySpec("D", "Selección por puntuación ajustada al riesgo", "score", 0, reselect_on_rank=True, reselect_margin=10),
    "E": StrategySpec("E", "Selección supervisada (proxy determinista de IA)", "ai_proxy", 2),
    "F": StrategySpec("F", "Referencia sin copy trading (mantener SOL / efectivo)", "none"),
}


@dataclass
class BTConfig:
    initial_capital: float = 52.0
    profile: str = "EQUILIBRADO"
    limits: RiskLimits | None = None
    copy: CopyParams = field(default_factory=CopyParams)
    switch: SwitchRules = field(default_factory=SwitchRules)
    exec: ExecConfig = field(default_factory=ExecConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    poll_seconds: float = 15.0  # wallet polling interval -> detection delay ~ U(0, poll) + rpc
    rpc_delay_s: float = 1.5
    rank_every_hours: float = 6.0
    lookback_days: float = 14.0
    check_every_minutes: float = 10.0
    warmup_days: float = 2.0
    seed: int = 1

    def resolved_limits(self) -> RiskLimits:
        return self.limits or PROFILES[self.profile]


@dataclass
class _Pos:
    mint: str
    wallet: str
    qty: float
    cost: float
    trader_qty: float
    opened: datetime
    last_px: float
    realized: float = 0.0


@dataclass
class BTResult:
    strategy: str
    name: str
    data_label: str
    start: str
    end: str
    initial_capital: float
    final_equity: float
    net_return_pct: float
    max_drawdown_pct: float
    volatility_daily_pct: float | None
    sharpe: float | None
    sortino: float | None
    profit_factor: float | None
    win_rate: float | None
    n_trades: int
    n_closed: int
    n_switches: int
    fees: float
    slippage: float
    avg_exposure_pct: float
    positive_day_ratio: float | None
    rejected_orders: int
    failed_orders: int
    equity_curve: list[tuple[str, float]]
    switch_log: list[dict[str, Any]]
    post_drop: dict[str, Any]
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Backtester:
    def __init__(self, data: Dataset, cfg: BTConfig):
        self.d = data
        self.cfg = cfg
        self.L = cfg.resolved_limits()

    # ---------- helpers ----------
    def _price(self, mint: str, ts: datetime, side: str | None = None) -> tuple[float, float] | None:
        """(price, gap_seconds) using information available at ts."""
        if isinstance(self.d, SyntheticDataset):
            p = self.d.price_exact(mint, ts)
            return (p, 0.0) if p else None
        hit = self.d.prices.at(mint, ts)
        if not hit:
            return None
        return hit[0], (ts - hit[1]).total_seconds()

    def _rank(self, now: datetime, by_wallet: dict[str, list[BTTrade]]) -> list[dict[str, Any]]:
        since = now - timedelta(days=self.cfg.lookback_days)
        out = []
        for w, trades in by_wallet.items():
            hist = [t for t in trades if since <= t.block_time < now]  # strictly before now
            if len(hist) < 4:
                continue
            m = compute_metrics(hist, now=now)
            liqs = [x for x in (self.d.liq(t.mint, t.block_time) for t in hist if t.kind == "BUY") if x]
            avg_liq = statistics.median(liqs) if liqs else None
            sr = score_trader(m, avg_liquidity_usd=avg_liq, now=now, cfg=self.cfg.scoring)
            out.append({"wallet": w, "score": sr.score, "eligible": sr.eligible, "pnl": m.realized_pnl, "roi": m.roi,
                        "n_closed": m.n_closed, "recent_roi": m.recent_roi, "data_quality": m.data_quality,
                        "top_share": m.top_token_share, "tpd": m.trades_per_day, "avg_usd": m.avg_trade_usd})
        return out

    def _pick(self, spec: StrategySpec, ranking: list[dict], exclude: set[str]) -> dict | None:
        pool = [r for r in ranking if r["wallet"] not in exclude]
        if spec.selection == "top_pnl":
            pool = [r for r in pool if r["n_closed"] >= 3]
            return max(pool, key=lambda r: r["pnl"], default=None)
        pool = [r for r in pool if r["eligible"]]
        if spec.selection == "ai_proxy":
            # deterministic stand-in for the AI veto: reject decaying, low-quality or concentrated traders
            pool = [r for r in sorted(pool, key=lambda r: r["score"], reverse=True)[:5]
                    if (r["recent_roi"] or 0) > 0 and r["data_quality"] >= 0.5 and (r["top_share"] or 1) < 0.5]
        return max(pool, key=lambda r: r["score"], default=None)

    # ---------- main ----------
    def run(self, spec: StrategySpec) -> BTResult:
        cfg, L, d = self.cfg, self.L, self.d
        rng = random.Random(cfg.seed)
        start = d.start + timedelta(days=cfg.warmup_days)
        end = d.end
        notes: list[str] = []
        if d.label.startswith("synthetic"):
            notes.append("DATOS SINTÉTICOS: valida la mecánica, no la rentabilidad real.")
        cash = cfg.initial_capital
        peak = cash
        positions: dict[str, _Pos] = {}
        fees = slip = 0.0
        closed_pnls: list[float] = []
        n_trades = rejected = failed = 0
        curve: list[tuple[datetime, float]] = []
        exposure_samples: list[float] = []
        switch_log: list[dict[str, Any]] = []
        day_start_eq, day = cash, start.date()

        if spec.selection == "none":
            return self._baseline(spec, start, end, notes)

        by_wallet: dict[str, list[BTTrade]] = defaultdict(list)
        for t in d.trades:
            by_wallet[t.wallet].append(t)
        events = [t for t in d.trades if start <= t.block_time <= end and t.kind in ("BUY", "SELL")]

        def equity(ts: datetime) -> float:
            val = 0.0
            for p in positions.values():
                px = self._price(p.mint, ts)
                if px:
                    p.last_px = px[0]
                val += p.qty * p.last_px
            return cash + val

        current: dict | None = None
        stats: SessionStats | None = None
        dropped_at: dict[str, datetime] = {}
        ranking: list[dict] = []
        next_rank = start
        next_check = start
        sw = SwitchRules(**{**cfg.switch.to_dict(), "consecutive_losses": spec.consecutive_losses})
        ei = 0

        def sell(p: _Pos, qty: float, ts: datetime, reason: str, copy_signal: bool) -> None:
            nonlocal cash, fees, slip, n_trades, failed
            px = self._price(p.mint, ts)
            if not px or qty <= 0:
                return
            ref = px[0] * math.exp(-(cfg.exec.adverse_bps_per_s / 1e4) * px[1]) if copy_signal else px[0]
            f = simulate("SELL", MarketContext(ref, d.liq(p.mint, ts)), rng, cfg.exec, qty=min(qty, p.qty),
                         max_slippage_bps=1500, max_impact=0.15, copy_signal=copy_signal)
            n_trades += 1
            fees += f.fee_usd
            if f.status == "failed":
                failed += 1
                cash -= f.fee_usd
                return
            frac = min(1.0, f.filled_qty / p.qty)
            proceeds = f.filled_usd - f.fee_usd
            pnl = proceeds - p.cost * frac
            slip += max(0.0, f.filled_qty * ref - f.filled_usd)
            cash += proceeds
            p.realized += pnl
            p.cost -= p.cost * frac
            p.qty -= f.filled_qty
            if p.qty <= 1e-9 * max(1.0, f.filled_qty) or frac > 0.999:
                positions.pop(p.mint, None)
                closed_pnls.append(p.realized)
                if stats is not None and current and p.wallet == current["wallet"]:
                    stats.on_close(p.realized)

        def close(p: _Pos, ts: datetime, reason: str, copy_signal: bool) -> None:
            sell(p, p.qty, ts, reason, copy_signal)

        def switch_to(ts: datetime, reason: str) -> None:
            nonlocal current, stats
            exclude = {w for w, t0 in dropped_at.items() if ts - t0 < timedelta(days=sw.cooldown_days)}
            if current:
                dropped_at[current["wallet"]] = ts
                exclude.add(current["wallet"])
                for p in list(positions.values()):
                    if p.wallet == current["wallet"]:
                        close(p, ts, "unfollow_exit", copy_signal=False)
            pick = self._pick(spec, ranking, exclude)
            eq = equity(ts)
            switch_log.append({"ts": ts.isoformat(), "reason": reason, "from": current["wallet"] if current else None,
                               "to": pick["wallet"] if pick else None, "score": pick["score"] if pick else None})
            current = pick
            stats = SessionStats(started_at=ts, equity_at_start=eq, score_at_start=pick["score"] if pick else None,
                                 trades_per_day_at_start=pick["tpd"] if pick else None,
                                 avg_trade_usd_at_start=pick["avg_usd"] if pick else None) if pick else None

        t = start
        while True:
            nxt = events[ei].block_time if ei < len(events) else None
            # periodic tasks happen before the next event
            boundary = min(x for x in (next_rank, next_check, nxt or end, end) if x is not None)
            t = boundary
            if t >= next_rank:
                ranking = self._rank(t, by_wallet)
                leader = self._pick(spec, ranking, {w for w, t0 in dropped_at.items() if t - t0 < timedelta(days=sw.cooldown_days)})
                if current is None and leader:
                    switch_to(t, "initial" if not switch_log else "no_active_trader")
                elif current and spec.reselect_on_rank and leader and leader["wallet"] != current["wallet"]:
                    cur = next((r for r in ranking if r["wallet"] == current["wallet"]), None)
                    key = "pnl" if spec.selection == "top_pnl" else "score"
                    if cur is None or leader[key] - cur[key] > spec.reselect_margin:
                        switch_to(t, "ranking_leader_changed")
                next_rank = t + timedelta(hours=cfg.rank_every_hours)
            if t >= next_check:
                for p in list(positions.values()):
                    px = self._price(p.mint, t)
                    if px and p.qty > 0:
                        trig = exit_triggers(p.cost / p.qty, px[0], L)
                        if trig:
                            close(p, t, trig, copy_signal=False)
                eq = equity(t)
                peak = max(peak, eq)
                if t.date() != day:
                    day, day_start_eq = t.date(), eq
                    if stats:
                        stats.day_pnl = 0.0
                curve.append((t, eq))
                exposure_samples.append((eq - cash) / eq if eq > 0 else 0)
                if current and stats:
                    last_tr = max((x.block_time for x in by_wallet[current["wallet"]] if x.block_time <= t), default=None)
                    dec = evaluate_switch(stats, sw, t, last_trader_trade_at=last_tr)
                    if dec.switch:
                        switch_to(t, dec.reason or "rule")
                next_check = t + timedelta(minutes=cfg.check_every_minutes)
            if nxt is not None and nxt <= t:
                ev = events[ei]
                ei += 1
                if current and ev.wallet == current["wallet"] and stats and ev.block_time >= stats.started_at:
                    delay = rng.uniform(0, cfg.poll_seconds) + cfg.rpc_delay_s
                    te = ev.block_time + timedelta(seconds=delay)
                    px = self._price(ev.mint, te)
                    if ev.kind == "BUY":
                        n_trades += 1
                        if not px:
                            rejected += 1
                        else:
                            ref = px[0] * math.exp((cfg.exec.adverse_bps_per_s / 1e4) * px[1])
                            eq = equity(te)
                            p0 = positions.get(ev.mint)
                            expo = eq - cash
                            req = EntryRequest(
                                mint=ev.mint, desired_usd=copy_buy_size(eq, ev.quote_usd, cfg.copy), equity=eq, cash=cash,
                                exposure_total=expo, exposure_token=(p0.qty * p0.last_px) if p0 else 0.0,
                                open_positions=len(positions), already_holding=p0 is not None, market_age_s=px[1],
                                signal_delay_s=delay, liquidity_usd=d.liq(ev.mint, te), token_age_minutes=None,
                                token_risk_score=d.token_risk(ev.mint, te), sellability=None,
                                daily_pnl_pct=(eq / day_start_eq - 1) * 100 if day_start_eq else 0,
                                drawdown_pct=(peak - eq) / peak * 100 if peak else 0, kill_switch=False, paused=False)
                            # recorded data: allow older observations, the adverse-drift model covers the gap
                            dec = check_entry(req, RiskLimits(**{**L.to_dict(), "max_data_age_s": 7200}))
                            if not dec.approved:
                                rejected += 1
                            else:
                                f = simulate("BUY", MarketContext(ref, d.liq(ev.mint, te)), rng, cfg.exec,
                                             usd=dec.size_usd, max_slippage_bps=L.max_slippage_bps,
                                             max_impact=L.max_price_impact_pct / 100)
                                fees += f.fee_usd
                                if f.status == "failed":
                                    failed += 1
                                    cash -= f.fee_usd
                                else:
                                    cash -= f.filled_usd + f.fee_usd
                                    slip += max(0.0, f.filled_usd - f.filled_qty * ref)
                                    p = positions.get(ev.mint) or _Pos(ev.mint, ev.wallet, 0, 0, 0, te, f.exec_price)
                                    p.qty += f.filled_qty
                                    p.cost += f.filled_usd + f.fee_usd
                                    p.trader_qty += ev.token_amount
                                    positions[ev.mint] = p
                    else:
                        p = positions.get(ev.mint)
                        if p and p.wallet == ev.wallet:
                            frac = sell_fraction(ev.token_amount, p.trader_qty)
                            p.trader_qty = max(0.0, p.trader_qty - ev.token_amount)
                            sell(p, p.qty if frac >= 1.0 else p.qty * frac, te, "copy_sell", True)
            if nxt is None and t >= end:
                break
            if t >= end and (nxt is None or nxt > end):
                break

        for p in list(positions.values()):
            close(p, end, "end_of_test", copy_signal=False)
        final = equity(end)
        curve.append((end, final))
        return self._summarize(spec, start, end, curve, final, closed_pnls, n_trades, len([s for s in switch_log if s["from"]]),
                               fees, slip, exposure_samples, rejected, failed, switch_log, notes, by_wallet, dropped_at)

    def _baseline(self, spec: StrategySpec, start: datetime, end: datetime, notes: list[str]) -> BTResult:
        cfg = self.cfg
        p0 = self._price(WSOL_MINT, start)
        curve: list[tuple[datetime, float]] = []
        if p0:
            qty = cfg.initial_capital * (1 - cfg.exec.dex_fee_bps / 1e4) / p0[0]
            t = start
            while t <= end:
                px = self._price(WSOL_MINT, t)
                if px:
                    curve.append((t, qty * px[0]))
                t += timedelta(hours=1)
            notes.append("Referencia: comprar y mantener SOL durante el periodo.")
        else:
            curve = [(start, cfg.initial_capital), (end, cfg.initial_capital)]
            notes.append("Sin serie de precios de SOL: la referencia es mantener efectivo (0%).")
        final = curve[-1][1] if curve else cfg.initial_capital
        return self._summarize(spec, start, end, curve, final, [], 1 if p0 else 0, 0, 0.0, 0.0,
                               [1.0 if p0 else 0.0], 0, 0, [], notes, {}, {})

    def _summarize(self, spec, start, end, curve, final, closed, n_trades, n_switches, fees, slip, expo,
                   rejected, failed, switch_log, notes, by_wallet, dropped_at) -> BTResult:
        cap = self.cfg.initial_capital
        peak, mdd = cap, 0.0
        daily: dict[str, float] = {}
        for ts, eq in curve:
            peak = max(peak, eq)
            mdd = max(mdd, (peak - eq) / peak if peak > 0 else 0)
            daily[ts.date().isoformat()] = eq
        days = sorted(daily)
        rets = []
        prev = cap
        for dk in days:
            rets.append(daily[dk] / prev - 1 if prev > 0 else 0)
            prev = daily[dk]
        vol = statistics.stdev(rets) if len(rets) >= 2 else None
        sharpe = sortino = None
        if len(rets) >= 10 and vol and vol > 0:
            sharpe = statistics.fmean(rets) / vol * math.sqrt(365)
            downs = [r for r in rets if r < 0]
            dd = math.sqrt(sum(r * r for r in downs) / len(rets)) if downs else 0
            sortino = statistics.fmean(rets) / dd * math.sqrt(365) if dd > 0 else None
        elif len(rets) < 10:
            notes.append("Menos de 10 días: Sharpe/Sortino no calculados.")
        wins = [p for p in closed if p > 0]
        losses = [-p for p in closed if p <= 0]
        pf = (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else (None if not wins else 99.0)
        post = self._post_drop(by_wallet, dropped_at)
        step = max(1, len(curve) // 400)
        return BTResult(
            spec.key, spec.name, self.d.label, start.isoformat(), end.isoformat(), cap, round(final, 4),
            round((final / cap - 1) * 100, 3), round(mdd * 100, 3), round(vol * 100, 3) if vol is not None else None,
            round(sharpe, 3) if sharpe is not None else None, round(sortino, 3) if sortino is not None else None,
            round(pf, 3) if pf is not None else None, round(len(wins) / len(closed), 3) if closed else None,
            n_trades, len(closed), n_switches, round(fees, 4), round(slip, 4),
            round(statistics.fmean(expo) * 100, 2) if expo else 0.0,
            round(sum(1 for r in rets if r > 0) / len(rets), 3) if rets else None, rejected, failed,
            [(ts.isoformat(), round(eq, 4)) for ts, eq in curve[::step]], switch_log[-200:], post, notes)

    def _post_drop(self, by_wallet: dict[str, list[BTTrade]], dropped_at: dict[str, datetime]) -> dict[str, Any]:
        """How did dropped traders do AFTER we dropped them (their own trades, next 3 days)?"""
        rois = []
        for w, t0 in dropped_at.items():
            after = [t for t in by_wallet.get(w, []) if t0 <= t.block_time <= t0 + timedelta(days=3)]
            m = compute_metrics(after) if after else None
            if m and m.n_closed:
                rois.append(m.roi or 0.0)
        if not rois:
            return {"n": 0}
        return {"n": len(rois), "mean_roi_after_drop": round(statistics.fmean(rois), 4),
                "share_positive_after_drop": round(sum(1 for r in rois if r > 0) / len(rois), 3)}


def run_all(data: Dataset, cfg: BTConfig, keys: list[str] | None = None) -> dict[str, Any]:
    bt = Backtester(data, cfg)
    results = {k: bt.run(STRATEGIES[k]).to_dict() for k in (keys or list(STRATEGIES))}
    return {"results": results, "comparison": compare(results), "config": {
        "profile": cfg.profile, "initial_capital": cfg.initial_capital, "poll_seconds": cfg.poll_seconds,
        "latency_ms": cfg.exec.latency_ms, "dex_fee_bps": cfg.exec.dex_fee_bps, "data": data.label}}


def bootstrap_diff(a_curve: list[tuple[str, float]], b_curve: list[tuple[str, float]], n: int = 2000, seed: int = 0) -> dict[str, Any] | None:
    """Paired bootstrap of daily-return differences (strategy - baseline)."""
    def daily(curve):
        out: dict[str, float] = {}
        for ts, eq in curve:
            out[ts[:10]] = eq
        return out
    da, db = daily(a_curve), daily(b_curve)
    keys = sorted(set(da) & set(db))
    if len(keys) < 6:
        return None
    diffs = []
    for i in range(1, len(keys)):
        ra = da[keys[i]] / da[keys[i - 1]] - 1
        rb = db[keys[i]] / db[keys[i - 1]] - 1
        diffs.append(ra - rb)
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choices(diffs, k=len(diffs))) for _ in range(n))
    lo, hi = means[int(0.025 * n)], means[int(0.975 * n)]
    return {"mean_daily_diff": statistics.fmean(diffs), "ci95": [lo, hi], "days": len(diffs),
            "significant": lo > 0 or hi < 0}


def compare(results: dict[str, dict]) -> dict[str, Any]:
    base = results.get("F")
    table = []
    for k, r in results.items():
        row = {k2: r[k2] for k2 in ("strategy", "name", "net_return_pct", "max_drawdown_pct", "sharpe", "sortino",
                                     "profit_factor", "n_trades", "n_switches", "fees", "slippage", "avg_exposure_pct")}
        if base and k != "F":
            row["vs_baseline"] = bootstrap_diff(r["equity_curve"], base["equity_curve"])
        table.append(row)
    return {"table": table, "warning": "Resultados históricos/simulados. No implican rentabilidad futura."}


def walk_forward(data: Dataset, cfg: BTConfig, folds: int = 3, candidates: tuple[int, ...] = (1, 2, 3, 4)) -> dict[str, Any]:
    """Choose N (consecutive-loss threshold) on each train window by return/drawdown, evaluate on the next window."""
    span = data.end - data.start
    seg = span / (folds + 1)
    out = []
    for i in range(folds):
        tr_s, tr_e = data.start, data.start + seg * (i + 1)
        te_s, te_e = tr_e, tr_e + seg
        def sub(s, e):
            ds = Dataset(data.trades, data.prices, data.liquidity, s, e, data.risk, data.exact_prices)
            ds.label = data.label
            if isinstance(data, SyntheticDataset):
                ds = _clone_synth(data, s, e)
            return ds
        best, best_obj = None, -1e9
        train_scores = {}
        for n in candidates:
            r = Backtester(sub(tr_s, tr_e), cfg).run(StrategySpec(f"N{n}", f"N={n}", "score", n))
            obj = r.net_return_pct - 0.5 * r.max_drawdown_pct
            train_scores[n] = round(obj, 3)
            if obj > best_obj:
                best, best_obj = n, obj
        test_cfg = BTConfig(**{**cfg.__dict__, "warmup_days": 0})
        test = Backtester(sub(te_s - timedelta(days=cfg.lookback_days), te_e), test_cfg)
        r_best = test.run(StrategySpec(f"N{best}", f"N={best}", "score", best))
        r_fixed = test.run(STRATEGIES["B"])
        out.append({"fold": i + 1, "train": [tr_s.isoformat(), tr_e.isoformat()], "test": [te_s.isoformat(), te_e.isoformat()],
                    "train_objective": train_scores, "chosen_n": best,
                    "oos_return_pct": r_best.net_return_pct, "oos_drawdown_pct": r_best.max_drawdown_pct,
                    "fixed_B_oos_return_pct": r_fixed.net_return_pct})
    agg = statistics.fmean(f["oos_return_pct"] for f in out) if out else None
    return {"folds": out, "mean_oos_return_pct": agg,
            "note": "Parámetro elegido solo con datos de entrenamiento; evaluado fuera de muestra."}


def _clone_synth(data: SyntheticDataset, s: datetime, e: datetime) -> SyntheticDataset:
    c = SyntheticDataset.__new__(SyntheticDataset)
    c.__dict__.update(data.__dict__)
    c.start, c.end = s, e
    return c
