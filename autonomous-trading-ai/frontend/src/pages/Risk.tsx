import { useEffect, useState } from "react";
import { api, fmtPct, fmtUsd, usePoll } from "../api";
import { Badge, Btn, Card, ErrorBox, Field, inputCls, Stat } from "../components/ui";

const LABELS: Record<string, string> = {
  max_trade_pct: "Máx. por operación %", max_token_pct: "Máx. por token %", max_total_exposure_pct: "Exposición total máx. %",
  max_daily_loss_pct: "Pérdida diaria máx. %", max_drawdown_pct: "Drawdown máx. %", stop_loss_pct: "Stop-loss %",
  take_profit_pct: "Take-profit % (0=off)", min_liquidity_usd: "Liquidez mínima $", max_price_impact_pct: "Impacto máx. %",
  max_slippage_bps: "Slippage máx. bps", max_token_risk_score: "Riesgo token máx. (0-100)", min_token_age_minutes: "Edad mínima token (min)",
  min_order_usd: "Orden mínima $", max_open_positions: "Posiciones abiertas máx.", max_data_age_s: "Antigüedad máx. datos (s)",
  max_signal_delay_s: "Retraso máx. señal (s)",
};

export default function Risk() {
  const { data, error, reload } = usePoll<any>("/risk", 15000);
  const [ov, setOv] = useState<Record<string, number> | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => {
    if (data && !ov) setOv(data.limits);
  }, [data, ov]);
  const saveProfile = async (profile: string) => {
    await api("/risk", { method: "PUT", body: { profile, overrides: {} } });
    setOv(null);
    reload();
  };
  const save = async () => {
    if (!ov || !data) return;
    const base = data.profiles[data.profile];
    const diff = Object.fromEntries(Object.entries(ov).filter(([k, v]) => base[k] !== v));
    const crit = Object.keys(diff).filter((k) => data.critical_keys.includes(k));
    if (crit.length && !confirm(`Vas a modificar límites críticos (${crit.join(", ")}). Quedará auditado. ¿Continuar?`)) return;
    try {
      await api("/risk", { method: "PUT", body: { overrides: diff } });
      setMsg("Límites guardados.");
      reload();
    } catch (e: any) {
      setMsg(e.message);
    }
  };
  const kill = async (active: boolean) => {
    if (active && !confirm("¿Activar la PARADA DE EMERGENCIA? Se bloquean todas las nuevas entradas.")) return;
    await api("/risk/kill", { body: { active } });
    reload();
  };
  const c = data?.current;
  return (
    <div className="space-y-3">
      <ErrorBox error={error} />
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <Stat label="Drawdown actual" value={fmtPct(c ? -c.drawdown_pct : null)} tone={c?.drawdown_pct > 0 ? "down" : null} sub={`límite ${data?.limits?.max_drawdown_pct}%`} />
        <Stat label="PnL del día" value={fmtPct(c?.daily_pnl_pct)} tone={c?.daily_pnl_pct < 0 ? "down" : c?.daily_pnl_pct > 0 ? "up" : null} sub={`límite -${data?.limits?.max_daily_loss_pct}%`} />
        <Stat label="Exposición" value={fmtUsd(c?.exposure)} sub={c?.equity ? `${((c.exposure / c.equity) * 100).toFixed(1)}% de ${fmtUsd(c.equity)}` : ""} />
        <Stat label="Estado" value={data?.kill_switch ? "PARADA" : data?.paused ? "PAUSADO" : "ACTIVO"} tone={data?.kill_switch ? "down" : data?.paused ? "warn" : "up"} />
      </div>
      <Card title="Parada de emergencia">
        <div className="flex flex-wrap items-center gap-3">
          {data?.kill_switch
            ? <Btn onClick={() => kill(false)}>Desactivar parada de emergencia</Btn>
            : <button onClick={() => kill(true)} className="rounded border-2 border-down bg-down/20 px-6 py-3 text-lg font-bold text-down hover:bg-down/40">PARADA DE EMERGENCIA</button>}
          <span className="text-sm text-slate-400">Bloquea nuevas entradas y pausa el agente. Las salidas por stop-loss siguen permitidas para reducir riesgo.</span>
        </div>
      </Card>
      <Card title="Perfil de riesgo">
        <div className="flex gap-2">
          {Object.keys(data?.profiles ?? {}).map((p) => (
            <button key={p} onClick={() => saveProfile(p)} className={`rounded border px-4 py-2 text-sm ${data?.profile === p ? "border-accent text-accent" : "border-edge text-slate-400"}`}>{p}</button>
          ))}
        </div>
        <div className="mt-1 text-xs text-slate-500">Sin martingala: el tamaño nunca aumenta para recuperar pérdidas.</div>
      </Card>
      <Card title="Límites (editables)">
        {ov && (
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            {Object.keys(LABELS).map((k) => (
              <Field key={k} label={LABELS[k]}>
                <div className="flex items-center gap-1">
                  <input className={`${inputCls} w-28`} type="number" step="any" value={ov[k] ?? ""} onChange={(e) => setOv({ ...ov, [k]: Number(e.target.value) })} />
                  {data.critical_keys.includes(k) && <Badge tone="warn">crítico</Badge>}
                </div>
              </Field>
            ))}
          </div>
        )}
        <div className="mt-3 flex items-center gap-2"><Btn kind="primary" onClick={save}>Guardar límites</Btn>{msg && <span className="text-xs text-slate-400">{msg}</span>}</div>
      </Card>
      <LiveStatus />
    </div>
  );
}

function LiveStatus() {
  const { data } = usePoll<any>("/live/status", 60000);
  return (
    <Card title="Ejecución real (desactivada)">
      <div className="text-sm text-slate-300">
        Estado: {data?.allowed ? <Badge tone="down">PERMITIDA</Badge> : <Badge tone="up">BLOQUEADA</Badge>} · conectada al agente: <b>{data?.wired_to_agent ? "sí" : "no"}</b>
      </div>
      <ul className="mt-2 grid grid-cols-1 gap-1 text-xs md:grid-cols-2">
        {Object.entries(data?.checks ?? {}).map(([k, v]) => <li key={k}>{v ? "✅" : "⛔"} {k}</li>)}
      </ul>
      <div className="mt-2 text-xs text-slate-500">Ver docs/LIVE_TRADING.md. No puede activarse desde Telegram ni desde este panel sin configuración del servidor.</div>
    </Card>
  );
}
