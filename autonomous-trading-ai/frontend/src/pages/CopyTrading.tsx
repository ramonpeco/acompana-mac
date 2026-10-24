import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ago, fmtPct, fmtUsd, short, usePoll } from "../api";
import { Badge, Btn, Card, ErrorBox, Field, inputCls, Pnl, Table } from "../components/ui";

const MULTS = [0.1, 0.25, 0.5, 1.0];

export default function CopyTrading() {
  const { data, error, reload } = usePoll<any>("/copy", 15000);
  const { data: orders } = usePoll<any[]>("/orders?limit=60", 15000);
  const [cp, setCp] = useState<any>(null);
  const [sr, setSr] = useState<any>(null);
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => {
    if (data && !cp) {
      setCp(data.copy_params);
      setSr(data.switch_rules);
    }
  }, [data, cp]);
  const save = async () => {
    try {
      await api("/copy", { method: "PUT", body: { copy_params: cp, switch_rules: sr } });
      setMsg("Guardado (auditado).");
      reload();
    } catch (e: any) {
      setMsg(e.message);
    }
  };
  const unfollow = async () => {
    if (!confirm("¿Dejar de seguir al trader actual? El agente seleccionará otro.")) return;
    await api("/copy/unfollow", { method: "POST" });
    reload();
  };
  const cur = data?.current;
  const num = (o: any, set: (x: any) => void, k: string, step = "1") => (
    <input className={`${inputCls} w-24`} type="number" step={step} value={o?.[k] ?? ""} onChange={(e) => set({ ...o, [k]: Number(e.target.value) })} />
  );
  return (
    <div className="space-y-3">
      <ErrorBox error={error} />
      <div className="grid gap-3 xl:grid-cols-3">
        <Card title="Trader actual" right={cur && <Btn kind="danger" onClick={unfollow}>Cambiar ahora</Btn>}>
          {cur ? (
            <div className="space-y-1 text-sm">
              <Link className="num text-accent hover:underline" to={`/traders/${cur.wallet}`}>{short(cur.wallet, 8)}</Link>
              <div>Seguido desde hace {ago(cur.started_at)} · modo {cur.selection?.mode}</div>
              <div className="num">Cerradas {cur.closed_trades} · G {cur.wins} / P {cur.losses} · racha {cur.consecutive_losses}</div>
              <div>PnL sesión <Pnl v={cur.realized_pnl}>{fmtUsd(cur.realized_pnl)}</Pnl></div>
              <div className="mt-2 rounded border border-edge p-2 text-xs text-slate-300">
                <div className="mb-1 text-slate-500">Motivo de selección {cur.selection?.explanation?.source === "ai" ? "(IA)" : "(determinista)"}</div>
                {cur.selection?.explanation?.text}
              </div>
              {cur.selection?.alternatives?.length > 0 && (
                <div className="text-xs text-slate-500">Alternativas: {cur.selection.alternatives.map((a: any) => `${short(a.wallet)} (${a.score?.toFixed(0)})`).join(", ")}</div>
              )}
            </div>
          ) : <div className="text-sm text-slate-500">Sin trader activo.</div>}
        </Card>

        <Card title="Multiplicador y tamaño">
          {cp && (
            <div className="space-y-2">
              <div className="flex flex-wrap gap-1">
                {MULTS.map((m) => (
                  <button key={m} onClick={() => setCp({ ...cp, multiplier: m })} className={`rounded border px-3 py-1 text-sm ${cp.multiplier === m ? "border-accent text-accent" : "border-edge text-slate-400"}`}>{m.toFixed(2)}x</button>
                ))}
                <input className={`${inputCls} w-20`} type="number" step="0.05" min="0.01" max="2" value={cp.multiplier} onChange={(e) => setCp({ ...cp, multiplier: Number(e.target.value) })} />
              </div>
              <div className="text-xs text-slate-500">Escala del tamaño de posición, no apalancamiento. A 1.00x, una compra del trader de {fmtUsd(cp.reference_trade_usd, 0)} equivale al {cp.base_order_pct}% del patrimonio; luego se aplican los límites de riesgo.</div>
              <div className="flex flex-wrap gap-2">
                <Field label="Referencia trader $">{num(cp, setCp, "reference_trade_usd")}</Field>
                <Field label="Orden base % patrimonio">{num(cp, setCp, "base_order_pct", "0.5")}</Field>
                <Field label="Modo">
                  <select className={inputCls} value={cp.sizing} onChange={(e) => setCp({ ...cp, sizing: e.target.value })}>
                    <option value="proportional">proporcional</option><option value="fixed">fijo</option>
                  </select>
                </Field>
              </div>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={cp.exit_on_unfollow} onChange={(e) => setCp({ ...cp, exit_on_unfollow: e.target.checked })} />Cerrar posiciones al dejar de seguir</label>
            </div>
          )}
        </Card>

        <Card title="Reglas de sustitución">
          {sr && (
            <div className="grid grid-cols-2 gap-2">
              <Field label="Pérdidas consecutivas (0=off)">{num(sr, setSr, "consecutive_losses")}</Field>
              <Field label="Base de pérdidas">
                <select className={inputCls} value={sr.loss_basis} onChange={(e) => setSr({ ...sr, loss_basis: e.target.value })}>
                  <option value="copied">copias propias</option><option value="trader">operaciones del trader</option>
                </select>
              </Field>
              <Field label="DD sesión %">{num(sr, setSr, "max_session_drawdown_pct", "0.5")}</Field>
              <Field label="Pérdida diaria sesión %">{num(sr, setSr, "max_session_daily_loss_pct", "0.5")}</Field>
              <Field label="Inactividad (h)">{num(sr, setSr, "inactivity_hours", "0.5")}</Field>
              <Field label="Observación mín. (ops)">{num(sr, setSr, "min_observation_trades")}</Field>
              <Field label="Seguimiento mín. (min)">{num(sr, setSr, "min_follow_minutes")}</Field>
              <Field label="Enfriamiento (días)">{num(sr, setSr, "cooldown_days", "0.5")}</Field>
              <Field label="Caída puntuación">{num(sr, setSr, "score_drop_points")}</Field>
              <Field label="Cambios máx./día">{num(sr, setSr, "max_switches_per_day")}</Field>
              <Field label="Selección">
                <select className={inputCls} value={sr.selection} onChange={(e) => setSr({ ...sr, selection: e.target.value })}>
                  <option value="score">puntuación ajustada al riesgo</option><option value="top_pnl">nº1 por PnL</option><option value="ai_supervised">supervisada por IA</option>
                </select>
              </Field>
            </div>
          )}
          <div className="mt-3 flex items-center gap-2"><Btn kind="primary" onClick={save}>Guardar</Btn>{msg && <span className="text-xs text-slate-400">{msg}</span>}</div>
        </Card>
      </div>

      <Card title="¿Fue beneficioso abandonar? (carteras sombra de traders descartados)">
        <Table head={["Trader", "Abandonado", "Motivo", "Si hubiéramos seguido", "Nuestra cartera desde entonces", "Veredicto", "Ops sombra"]} empty={!data?.shadow_comparison?.length}>
          {(data?.shadow_comparison ?? []).map((s: any) => (
            <tr key={s.wallet + s.ended_at}>
              <td className="num">{short(s.wallet, 5)}</td><td>{ago(s.ended_at)}</td><td>{s.end_reason}</td>
              <td><Pnl v={s.shadow_return_pct}>{fmtPct(s.shadow_return_pct)}</Pnl></td>
              <td><Pnl v={s.main_return_since_pct}>{fmtPct(s.main_return_since_pct)}</Pnl></td>
              <td><Badge tone={s.switch_was_beneficial ? "up" : "down"}>{s.switch_was_beneficial ? "acertado" : "peor"}</Badge>{s.shadow_active && <Badge>en curso</Badge>}</td>
              <td className="num">{s.shadow_trades}</td>
            </tr>
          ))}
        </Table>
      </Card>

      <Card title="Historial de cambios">
        <Table head={["Trader", "Inicio", "Fin", "Motivo fin", "Puntuación", "Cerradas", "G/P", "PnL"]} empty={!data?.history?.length}>
          {(data?.history ?? []).map((h: any) => (
            <tr key={h.id}>
              <td><Link className="num text-accent hover:underline" to={`/traders/${h.wallet}`}>{short(h.wallet, 5)}</Link></td>
              <td>{h.started_at?.slice(0, 16).replace("T", " ")}</td><td>{h.ended_at ? h.ended_at.slice(0, 16).replace("T", " ") : <Badge tone="info">activo</Badge>}</td>
              <td>{h.end_reason ?? "—"}</td><td className="num">{h.score_at_start?.toFixed(1)}</td><td className="num">{h.closed_trades}</td>
              <td className="num">{h.wins}/{h.losses}</td><td><Pnl v={h.realized_pnl}>{fmtUsd(h.realized_pnl)}</Pnl></td>
            </tr>
          ))}
        </Table>
      </Card>

      <Card title="Operaciones replicadas (simuladas)">
        <Table head={["Hora", "Lado", "Token", "Estado", "USD", "Precio trader", "Precio ref.", "Ejecución", "Slip. bps", "Retraso señal", "Latencia", "Motivo"]} empty={!orders?.length}>
          {(orders ?? []).map((o) => (
            <tr key={o.id}>
              <td>{ago(o.executed_at)}</td>
              <td><Badge tone={o.side === "BUY" ? "up" : "down"}>{o.side}</Badge></td>
              <td className="num">{short(o.mint)}</td>
              <td><Badge tone={o.status === "filled" ? "up" : o.status === "partial" ? "warn" : o.status === "rejected" ? "neutral" : "down"}>{o.status}</Badge></td>
              <td className="num">{fmtUsd(o.filled_usd || o.requested_usd)}</td>
              <td className="num">{o.trader_price?.toPrecision(4) ?? "—"}</td>
              <td className="num">{o.reference_price?.toPrecision(4) ?? "—"}</td>
              <td className="num">{o.exec_price?.toPrecision(4) ?? "—"}</td>
              <td className="num">{o.slippage_bps?.toFixed(0) ?? "—"}</td>
              <td className="num">{o.signal_delay_s == null ? "—" : `${o.signal_delay_s.toFixed(0)}s`}</td>
              <td className="num">{o.latency_ms ? `${o.latency_ms}ms` : "—"}</td>
              <td className="max-w-xs truncate text-xs text-slate-500" title={o.reject_reason ?? ""}>{o.reject_reason ?? o.reason}</td>
            </tr>
          ))}
        </Table>
      </Card>
    </div>
  );
}
