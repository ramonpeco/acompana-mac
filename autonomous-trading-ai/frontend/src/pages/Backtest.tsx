import { useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, fmtPct, fmtUsd, usePoll } from "../api";
import { Badge, Btn, Card, ErrorBox, Field, inputCls, Pnl, Table } from "../components/ui";

const COLORS: Record<string, string> = { A: "#38bdf8", B: "#f59e0b", C: "#a78bfa", D: "#22c55e", E: "#f472b6", F: "#94a3b8" };
const NAMES: Record<string, string> = {
  A: "A · Siempre nº1", B: "B · 2 pérdidas", C: "C · 3 pérdidas", D: "D · Puntuación", E: "E · Supervisada IA", F: "F · Referencia",
};

export default function Backtest() {
  const [p, setP] = useState({ source: "synthetic", regime: "neutral", days: 10, profile: "EQUILIBRADO", initial_capital: 52, latency_ms: 2500, poll_seconds: 15, dex_fee_bps: 25, seed: 7, walk_forward: false });
  const [strats, setStrats] = useState<string[]>(["A", "B", "C", "D", "E", "F"]);
  const [res, setRes] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { data: hist, reload } = usePoll<any[]>("/backtests", 0);
  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      setRes(await api("/backtest", { body: { ...p, strategies: strats } }));
      reload();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const load = async (id: number) => setRes(await api(`/backtests/${id}`));
  const chart = (() => {
    if (!res) return [];
    const byTs: Record<string, any> = {};
    for (const [k, r] of Object.entries<any>(res.results)) for (const [ts, eq] of r.equity_curve) (byTs[ts.slice(0, 13)] ??= { t: ts.slice(0, 13).replace("T", " ") })[k] = eq;
    return Object.values(byTs).sort((a: any, b: any) => (a.t < b.t ? -1 : 1));
  })();
  const set = (k: string, v: any) => setP({ ...p, [k]: v });
  return (
    <div className="space-y-3">
      <Card title="Configuración">
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Datos">
            <select className={inputCls} value={p.source} onChange={(e) => set("source", e.target.value)}>
              <option value="synthetic">sintéticos (mecánica)</option><option value="recorded">registrados por el agente</option>
            </select>
          </Field>
          {p.source === "synthetic" && (
            <Field label="Escenario">
              <select className={inputCls} value={p.regime} onChange={(e) => set("regime", e.target.value)}>
                <option value="neutral">neutral</option><option value="favorable">favorable</option><option value="unfavorable">desfavorable</option>
              </select>
            </Field>
          )}
          <Field label="Días"><input className={`${inputCls} w-20`} type="number" value={p.days} onChange={(e) => set("days", Number(e.target.value))} /></Field>
          <Field label="Capital $"><input className={`${inputCls} w-24`} type="number" value={p.initial_capital} onChange={(e) => set("initial_capital", Number(e.target.value))} /></Field>
          <Field label="Perfil">
            <select className={inputCls} value={p.profile} onChange={(e) => set("profile", e.target.value)}>
              <option>CONSERVADOR</option><option>EQUILIBRADO</option><option>AGRESIVO</option>
            </select>
          </Field>
          <Field label="Latencia ms"><input className={`${inputCls} w-24`} type="number" value={p.latency_ms} onChange={(e) => set("latency_ms", Number(e.target.value))} /></Field>
          <Field label="Sondeo s"><input className={`${inputCls} w-20`} type="number" value={p.poll_seconds} onChange={(e) => set("poll_seconds", Number(e.target.value))} /></Field>
          <Field label="Comisión DEX bps"><input className={`${inputCls} w-20`} type="number" value={p.dex_fee_bps} onChange={(e) => set("dex_fee_bps", Number(e.target.value))} /></Field>
          <Field label="Semilla"><input className={`${inputCls} w-20`} type="number" value={p.seed} onChange={(e) => set("seed", Number(e.target.value))} /></Field>
          <label className="flex items-center gap-1 text-sm"><input type="checkbox" checked={p.walk_forward} onChange={(e) => set("walk_forward", e.target.checked)} />walk-forward</label>
        </div>
        <div className="mt-3 flex flex-wrap gap-1">
          {Object.keys(NAMES).map((k) => (
            <button key={k} onClick={() => setStrats(strats.includes(k) ? strats.filter((x) => x !== k) : [...strats, k])}
              className={`rounded border px-2 py-1 text-xs ${strats.includes(k) ? "border-accent text-accent" : "border-edge text-slate-500"}`}>{NAMES[k]}</button>
          ))}
          <Btn kind="primary" onClick={run} disabled={busy || !strats.length}>{busy ? "Ejecutando…" : "Ejecutar backtest"}</Btn>
        </div>
        <div className="mt-2 text-xs text-slate-500">Sin información futura: el ranking usa solo datos anteriores a cada decisión y las copias se ejecutan tras el retraso de detección, nunca al precio del trader.</div>
      </Card>
      <ErrorBox error={error} />
      {res && (
        <>
          <div className="flex items-center gap-2 text-sm">
            <Badge tone={String(res.results?.[strats[0]]?.data_label ?? res.config?.data).startsWith("synthetic") ? "warn" : "info"}>{res.config?.data}</Badge>
            <span className="text-slate-400">{res.comparison?.warning}</span>
          </div>
          <Card title="Curvas de patrimonio">
            <div className="h-72">
              <ResponsiveContainer>
                <LineChart data={chart}>
                  <CartesianGrid stroke="#1c2430" />
                  <XAxis dataKey="t" stroke="#475569" fontSize={11} minTickGap={60} />
                  <YAxis stroke="#475569" fontSize={11} domain={["auto", "auto"]} width={50} />
                  <Tooltip contentStyle={{ background: "#0d1117", border: "1px solid #1c2430" }} />
                  <Legend />
                  {Object.keys(res.results).map((k) => <Line key={k} dataKey={k} name={NAMES[k]} stroke={COLORS[k]} dot={false} connectNulls isAnimationActive={false} />)}
                </LineChart>
              </ResponsiveContainer>
            </div>
          </Card>
          <Card title="Comparación de estrategias">
            <Table head={["Estrategia", "Rent. neta", "Final", "Max DD", "Vol. diaria", "Sharpe", "Sortino", "PF", "Acierto", "Ops", "Cambios", "Comisiones", "Slippage", "Exposición", "Días +", "vs referencia (IC95)"]}>
              {Object.entries<any>(res.results).map(([k, r]) => {
                const vs = res.comparison.table.find((x: any) => x.strategy === k)?.vs_baseline;
                return (
                  <tr key={k}>
                    <td style={{ color: COLORS[k] }}>{NAMES[k]}</td>
                    <td><Pnl v={r.net_return_pct}>{fmtPct(r.net_return_pct)}</Pnl></td>
                    <td className="num">{fmtUsd(r.final_equity)}</td>
                    <td className="num text-down">{r.max_drawdown_pct.toFixed(2)}%</td>
                    <td className="num">{r.volatility_daily_pct?.toFixed(2) ?? "—"}</td>
                    <td className="num">{r.sharpe?.toFixed(2) ?? "—"}</td>
                    <td className="num">{r.sortino?.toFixed(2) ?? "—"}</td>
                    <td className="num">{r.profit_factor?.toFixed(2) ?? "—"}</td>
                    <td className="num">{r.win_rate == null ? "—" : `${(r.win_rate * 100).toFixed(0)}%`}</td>
                    <td className="num">{r.n_trades}</td><td className="num">{r.n_switches}</td>
                    <td className="num">{fmtUsd(r.fees)}</td><td className="num">{fmtUsd(r.slippage)}</td>
                    <td className="num">{r.avg_exposure_pct.toFixed(0)}%</td>
                    <td className="num">{r.positive_day_ratio == null ? "—" : `${(r.positive_day_ratio * 100).toFixed(0)}%`}</td>
                    <td className="num text-xs">{vs ? <span className={vs.significant ? "text-accent" : "text-slate-500"}>{(vs.mean_daily_diff * 100).toFixed(2)}%/día [{(vs.ci95[0] * 100).toFixed(2)}, {(vs.ci95[1] * 100).toFixed(2)}]{vs.significant ? " *" : " n.s."}</span> : "—"}</td>
                  </tr>
                );
              })}
            </Table>
            <div className="mt-2 space-y-1 text-xs text-slate-500">
              {Object.entries<any>(res.results).map(([k, r]) => r.post_drop?.n ? <div key={k}>{NAMES[k]}: {r.post_drop.n} traders abandonados; ROI medio posterior {(r.post_drop.mean_roi_after_drop * 100).toFixed(1)}% ({(r.post_drop.share_positive_after_drop * 100).toFixed(0)}% positivos).</div> : null)}
              {[...new Set(Object.values<any>(res.results).flatMap((r) => r.notes))].map((n) => <div key={n as string}>• {n as string}</div>)}
            </div>
          </Card>
          {res.walk_forward && (
            <Card title="Walk-forward (fuera de muestra)">
              <Table head={["Fold", "Entrenamiento", "Prueba", "Objetivo train por N", "N elegido", "OOS rent.", "OOS DD", "B fijo OOS"]}>
                {res.walk_forward.folds.map((f: any) => (
                  <tr key={f.fold}>
                    <td>{f.fold}</td><td className="text-xs">{f.train[0].slice(0, 10)} → {f.train[1].slice(0, 10)}</td>
                    <td className="text-xs">{f.test[0].slice(0, 10)} → {f.test[1].slice(0, 10)}</td>
                    <td className="num text-xs">{Object.entries(f.train_objective).map(([n, v]) => `${n}:${v}`).join(" ")}</td>
                    <td className="num">{f.chosen_n}</td><td><Pnl v={f.oos_return_pct}>{fmtPct(f.oos_return_pct)}</Pnl></td>
                    <td className="num">{f.oos_drawdown_pct.toFixed(2)}%</td><td><Pnl v={f.fixed_B_oos_return_pct}>{fmtPct(f.fixed_B_oos_return_pct)}</Pnl></td>
                  </tr>
                ))}
              </Table>
              <div className="mt-1 text-xs text-slate-500">{res.walk_forward.note}</div>
            </Card>
          )}
        </>
      )}
      <Card title="Ejecuciones anteriores">
        <Table head={["ID", "Fecha", "Datos", "Resumen"]} empty={!hist?.length}>
          {(hist ?? []).map((h) => (
            <tr key={h.id} className="cursor-pointer" onClick={() => load(h.id)}>
              <td className="num">{h.id}</td><td>{h.created_at?.slice(0, 16).replace("T", " ")}</td><td>{h.data_source} {h.params?.regime}</td>
              <td className="num text-xs">{h.summary.map((s: any) => `${s.strategy}:${s.net_return_pct?.toFixed(1)}%`).join("  ")}</td>
            </tr>
          ))}
        </Table>
      </Card>
    </div>
  );
}
