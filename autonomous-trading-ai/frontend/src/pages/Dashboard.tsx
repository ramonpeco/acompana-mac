import { Link } from "react-router-dom";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ago, fmtNum, fmtPct, fmtUsd, short, useLiveEvents, usePoll } from "../api";
import { Badge, Card, ErrorBox, Pnl, Stat, Table, tone } from "../components/ui";

const axis = { stroke: "#475569", fontSize: 11 };

export default function Dashboard() {
  const { data: d, error } = usePoll<any>("/dashboard", 10000);
  const { data: open } = usePoll<any[]>("/positions?status=open", 10000);
  const { data: closed } = usePoll<any[]>("/positions?status=closed&limit=10", 15000);
  const { events, connected } = useLiveEvents(30);
  const p = d?.portfolio;
  const curve = (d?.equity_curve ?? []).map((x: any) => ({ ...x, t: new Date(x.ts).toLocaleString() }));
  return (
    <div className="space-y-3">
      <ErrorBox error={error} />
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8">
        <Stat label="Patrimonio" value={fmtUsd(p?.equity)} sub={`inicial ${fmtUsd(p?.initial_capital)}`} />
        <Stat label="PnL" value={fmtUsd(p?.pnl)} tone={tone(p?.pnl)} sub={`real. ${fmtUsd(p?.realized)}`} />
        <Stat label="Rentabilidad" value={fmtPct(p?.return_pct)} tone={tone(p?.return_pct)} sub={`día ${fmtPct(p?.daily_pnl_pct)}`} />
        <Stat label="Drawdown" value={fmtPct(p?.drawdown_pct ? -p.drawdown_pct : 0)} tone={p?.drawdown_pct > 0 ? "down" : null} sub={`pico ${fmtUsd(p?.peak_equity)}`} />
        <Stat label="Abiertas" value={p?.open_positions ?? "—"} sub={`expuesto ${fmtUsd(p?.exposure)}`} />
        <Stat label="Cerradas" value={p?.closed_positions ?? "—"} />
        <Stat label="Tasa acierto" value={p?.win_rate == null ? "—" : `${(p.win_rate * 100).toFixed(0)}%`} />
        <Stat label="Costes" value={fmtUsd((p?.fees ?? 0) + (p?.slippage ?? 0))} sub={`com. ${fmtUsd(p?.fees)} · slip. ${fmtUsd(p?.slippage)}`} />
      </div>

      <div className="grid gap-3 xl:grid-cols-3">
        <Card title="Evolución del patrimonio (simulado)" className="xl:col-span-2">
          <div className="h-64">
            <ResponsiveContainer>
              <AreaChart data={curve}>
                <defs>
                  <linearGradient id="eq" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#f59e0b" stopOpacity={0.35} />
                    <stop offset="100%" stopColor="#f59e0b" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke="#1c2430" />
                <XAxis dataKey="t" {...axis} minTickGap={60} />
                <YAxis {...axis} domain={["auto", "auto"]} width={50} />
                <Tooltip contentStyle={{ background: "#0d1117", border: "1px solid #1c2430" }} />
                <Area type="monotone" dataKey="equity" stroke="#f59e0b" fill="url(#eq)" dot={false} isAnimationActive={false} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
          <div className="h-24">
            <ResponsiveContainer>
              <AreaChart data={curve}>
                <XAxis dataKey="t" hide />
                <YAxis {...axis} reversed width={50} />
                <Tooltip contentStyle={{ background: "#0d1117", border: "1px solid #1c2430" }} />
                <Area type="monotone" dataKey="drawdown_pct" name="drawdown %" stroke="#ef4444" fill="#ef444433" dot={false} isAnimationActive={false} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </Card>

        <div className="space-y-3">
          <Card title="Trader seguido">
            {d?.follow ? (
              <div className="space-y-1 text-sm">
                <Link to={`/traders/${d.follow.wallet}`} className="num text-accent hover:underline">{short(d.follow.wallet, 6)}</Link>
                <div className="text-slate-400">desde hace {ago(d.follow.started_at)} · puntuación {d.follow.score_at_start?.toFixed(1)}</div>
                <div className="num">G {d.follow.wins} / P {d.follow.losses} · racha pérdidas <b className={d.follow.consecutive_losses ? "text-down" : ""}>{d.follow.consecutive_losses}</b></div>
                <div>PnL sesión <Pnl v={d.follow.realized_pnl}>{fmtUsd(d.follow.realized_pnl)}</Pnl></div>
              </div>
            ) : <div className="text-sm text-slate-500">Ninguno: el agente está buscando candidatos elegibles.</div>}
          </Card>
          <Card title="Agente" right={<Badge tone={connected ? "up" : "down"}>{connected ? "en vivo" : "sin WS"}</Badge>}>
            <div className="grid grid-cols-2 gap-1 text-sm">
              <span className="text-slate-500">Estado</span><span>{d?.agent?.state}</span>
              <span className="text-slate-500">Último ciclo</span><span>{ago(d?.agent?.last_tick)}</span>
              <span className="text-slate-500">Ciclos</span><span className="num">{d?.agent?.ticks}</span>
              <span className="text-slate-500">Perfil</span><span>{d?.profile}</span>
              <span className="text-slate-500">IA</span><span>{d?.ai_provider}</span>
              <span className="text-slate-500">Telegram</span><span>{d?.telegram ? "conectado" : "no"}</span>
            </div>
            {d?.agent?.last_error && <div className="mt-2 text-xs text-down">{d.agent.last_error}</div>}
          </Card>
          <Card title="Conectores">
            <Table head={["Fuente", "Estado", "Peticiones", "Errores", "Latencia"]}>
              {(d?.connectors ?? []).map((c: any) => (
                <tr key={c.name}>
                  <td>{c.name}</td>
                  <td><Badge tone={c.status === "ok" ? "up" : c.status === "idle" ? "neutral" : c.status === "degraded" ? "warn" : "down"}>{c.status}</Badge></td>
                  <td className="num">{c.requests}</td>
                  <td className="num" title={c.last_error ?? ""}>{c.errors}</td>
                  <td className="num">{c.avg_latency_ms != null ? `${c.avg_latency_ms}ms` : "—"}</td>
                </tr>
              ))}
            </Table>
          </Card>
        </div>
      </div>

      <div className="grid gap-3 xl:grid-cols-2">
        <Card title="Operaciones abiertas">
          <Table head={["Token", "Cantidad", "Coste", "Valor", "PnL no real.", "Abierta"]} empty={!open?.length}>
            {(open ?? []).map((x) => (
              <tr key={x.id}>
                <td>{x.symbol ?? short(x.mint)}</td><td className="num">{fmtNum(x.qty)}</td>
                <td className="num">{fmtUsd(x.cost_basis)}</td><td className="num">{fmtUsd(x.value)}</td>
                <td><Pnl v={x.unrealized}>{fmtUsd(x.unrealized)}</Pnl></td><td>{ago(x.opened_at)}</td>
              </tr>
            ))}
          </Table>
        </Card>
        <Card title="Últimas operaciones cerradas">
          <Table head={["Token", "Invertido", "Recuperado", "PnL", "Motivo", "Cerrada"]} empty={!closed?.length}>
            {(closed ?? []).map((x) => (
              <tr key={x.id}>
                <td>{x.symbol ?? short(x.mint)}</td><td className="num">{fmtUsd(x.invested)}</td>
                <td className="num">{fmtUsd(x.proceeds)}</td><td><Pnl v={x.realized_pnl}>{fmtUsd(x.realized_pnl)}</Pnl></td>
                <td>{x.close_reason}</td><td>{ago(x.closed_at)}</td>
              </tr>
            ))}
          </Table>
        </Card>
      </div>

      <Card title="Actividad en tiempo real">
        <div className="max-h-64 space-y-1 overflow-y-auto text-sm">
          {events.length === 0 && <div className="text-slate-500">Esperando eventos del agente…</div>}
          {events.map((e, i) => (
            <div key={i} className="flex gap-2">
              <span className="num shrink-0 text-xs text-slate-600">{e.ts?.slice(11, 19)}</span>
              <Badge tone={e.level === "alert" || e.level === "error" ? "down" : e.level === "warn" ? "warn" : "neutral"}>{e.kind}</Badge>
              <span className="text-slate-300">{e.message}</span>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
