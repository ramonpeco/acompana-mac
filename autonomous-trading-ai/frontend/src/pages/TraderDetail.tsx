import { useParams } from "react-router-dom";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ago, fmtNum, fmtPct, fmtUsd, short, usePoll } from "../api";
import { Badge, Card, ErrorBox, Pnl, Stat, Table } from "../components/ui";

const axis = { stroke: "#475569", fontSize: 11 };

export default function TraderDetail() {
  const { address } = useParams();
  const { data: t, error } = usePoll<any>(`/traders/${address}`, 30000);
  const m = t?.metrics ?? {};
  const c = m.components ?? {};
  return (
    <div className="space-y-3">
      <ErrorBox error={error} />
      <div className="flex flex-wrap items-center gap-2">
        <span className="num text-lg text-accent">{t?.label ?? short(address, 8)}</span>
        <Badge tone={t?.status === "followed" ? "info" : t?.status === "candidate" ? "up" : "neutral"}>{t?.status}</Badge>
        {m.verified ? <Badge tone="up">historial suficiente</Badge> : <Badge tone="warn">no verificado</Badge>}
        <span className="num text-xs text-slate-500">{address}</span>
      </div>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8">
        <Stat label="Puntuación" value={t?.score?.toFixed(1) ?? "—"} />
        <Stat label="ROI realizado" value={m.roi == null ? "—" : fmtPct(m.roi * 100)} tone={m.roi > 0 ? "up" : m.roi < 0 ? "down" : null} />
        <Stat label="PnL realizado" value={fmtUsd(m.realized_pnl)} sub={`no real. ${fmtUsd(m.unrealized_pnl)}`} />
        <Stat label="Acierto" value={m.win_rate == null ? "—" : `${(m.win_rate * 100).toFixed(0)}%`} sub={`${m.n_closed ?? 0} cerradas`} />
        <Stat label="Profit factor" value={m.profit_factor?.toFixed(2) ?? "—"} sub={`gan. med ${fmtUsd(m.avg_win)} · pérd. ${fmtUsd(m.avg_loss)}`} />
        <Stat label="Drawdown" value={m.max_drawdown_pct == null ? "—" : `${m.max_drawdown_pct.toFixed(1)}%`} sub={`vol ret ${fmtNum(m.ret_volatility)}`} />
        <Stat label="Mantenimiento" value={m.median_hold_hours == null ? "—" : `${(m.median_hold_hours * 60).toFixed(0)} min`} sub={`${fmtNum(m.trades_per_day)} compras/día`} />
        <Stat label="Calidad datos" value={m.data_quality?.toFixed(2) ?? "—"} sub={`base conocida ${m.known_basis_ratio == null ? "—" : (m.known_basis_ratio * 100).toFixed(0) + "%"}`} />
      </div>
      <div className="grid gap-3 xl:grid-cols-3">
        <Card title="Curva de PnL estimada (operaciones cerradas)" className="xl:col-span-2">
          <div className="h-64">
            <ResponsiveContainer>
              <LineChart data={(t?.equity_curve_estimated ?? []).map((x: any) => ({ ...x, t: new Date(x.ts).toLocaleString() }))}>
                <CartesianGrid stroke="#1c2430" />
                <XAxis dataKey="t" {...axis} minTickGap={60} />
                <YAxis {...axis} width={50} />
                <Tooltip contentStyle={{ background: "#0d1117", border: "1px solid #1c2430" }} />
                <Line dataKey="cum_pnl" stroke="#22c55e" dot={false} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
          <div className="text-[11px] text-slate-500">{t?.note} Tokens con coste desconocido: {t?.unknown_basis_tokens ?? 0}.</div>
        </Card>
        <Card title="Componentes de la puntuación">
          {Object.entries(c).map(([k, v]: any) => (
            <div key={k} className="mb-1">
              <div className="flex justify-between text-xs text-slate-400"><span>{k}</span><span className="num">{(v * 100).toFixed(0)}</span></div>
              <div className="h-1.5 rounded bg-edge"><div className="h-1.5 rounded bg-accent" style={{ width: `${v * 100}%` }} /></div>
            </div>
          ))}
          {(t?.excluded_reasons ?? []).length > 0 && (
            <div className="mt-2 text-xs text-down">{t.excluded_reasons.map((r: string) => <div key={r}>• {r}</div>)}</div>
          )}
          <div className="mt-2 text-xs text-slate-500">Concentración top token {m.top_token_share == null ? "—" : (m.top_token_share * 100).toFixed(0) + "%"} · historial {fmtNum(m.history_days, 1)} días · días positivos {m.positive_day_ratio == null ? "—" : (m.positive_day_ratio * 100).toFixed(0) + "%"}</div>
        </Card>
      </div>
      <div className="grid gap-3 xl:grid-cols-2">
        <Card title="Operaciones cerradas (round trips)">
          <Table head={["Token", "Abierta", "Invertido", "Recuperado", "PnL", "Ret.", "Horas"]} empty={!t?.round_trips?.length}>
            {(t?.round_trips ?? []).slice().reverse().map((r: any, i: number) => (
              <tr key={i}>
                <td>{r.symbol ?? short(r.mint)}</td><td>{ago(r.opened_at)}</td><td className="num">{fmtUsd(r.invested)}</td>
                <td className="num">{fmtUsd(r.proceeds)}</td><td><Pnl v={r.pnl}>{fmtUsd(r.pnl)}</Pnl></td>
                <td><Pnl v={r.ret}>{fmtPct(r.ret * 100, 1)}</Pnl></td><td className="num">{r.hold_hours.toFixed(1)}</td>
              </tr>
            ))}
          </Table>
        </Card>
        <Card title="Actividad reciente on-chain">
          <Table head={["Hora", "Tipo", "Token", "Cantidad", "USD", "Tx"]} empty={!t?.recent_trades?.length}>
            {(t?.recent_trades ?? []).map((r: any) => (
              <tr key={r.signature + r.mint}>
                <td>{ago(r.block_time)}</td>
                <td><Badge tone={r.kind === "BUY" ? "up" : r.kind === "SELL" ? "down" : "neutral"}>{r.kind}</Badge></td>
                <td>{r.symbol ?? short(r.mint)}</td><td className="num">{fmtNum(r.token_amount)}</td>
                <td className="num">{r.quote_usd == null ? "—" : `${fmtUsd(r.quote_usd)}${r.usd_is_estimate ? "*" : ""}`}</td>
                <td><a className="num text-xs text-slate-500 hover:text-accent" href={`https://solscan.io/tx/${r.signature}`} target="_blank" rel="noreferrer">{short(r.signature, 4)}</a></td>
              </tr>
            ))}
          </Table>
          <div className="mt-1 text-[11px] text-slate-500">* USD estimado con el precio de SOL en el momento de la ingesta.</div>
        </Card>
      </div>
    </div>
  );
}
