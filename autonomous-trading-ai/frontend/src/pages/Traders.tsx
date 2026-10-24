import { useState } from "react";
import { Link } from "react-router-dom";
import { api, ago, fmtPct, fmtUsd, short, usePoll } from "../api";
import { Badge, Btn, Card, ErrorBox, Field, inputCls, Pnl, Table } from "../components/ui";

export default function Traders() {
  const [status, setStatus] = useState("");
  const { data, error, reload } = usePoll<any[]>(`/traders${status ? `?status=${status}` : ""}`, 30000);
  const [addr, setAddr] = useState("");
  const [label, setLabel] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const add = async () => {
    setMsg("Sincronizando historial…");
    try {
      const r = await api("/traders", { body: { address: addr.trim(), label: label || null } });
      setMsg(r.synced ? `Añadida. Puntuación ${r.score?.toFixed?.(1) ?? "—"} (${r.status})` : `Añadida, sincronización pendiente: ${r.error}`);
      setAddr("");
      reload();
    } catch (e: any) {
      setMsg(e.message);
    }
  };
  return (
    <div className="space-y-3">
      <Card title="Añadir wallet manualmente">
        <div className="flex flex-wrap items-end gap-2">
          <Field label="Dirección Solana"><input className={`${inputCls} w-96 max-w-full`} value={addr} onChange={(e) => setAddr(e.target.value)} /></Field>
          <Field label="Etiqueta"><input className={inputCls} value={label} onChange={(e) => setLabel(e.target.value)} /></Field>
          <Btn kind="primary" onClick={add} disabled={!addr}>Añadir y analizar</Btn>
          {msg && <span className="text-sm text-slate-400">{msg}</span>}
        </div>
      </Card>
      <ErrorBox error={error} />
      <Card title="Ranking de traders" right={
        <select className={inputCls} value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">todos</option><option value="candidate">elegibles</option><option value="followed">seguido</option><option value="excluded">excluidos</option>
        </select>}>
        <Table head={["#", "Wallet", "Puntuación", "Estado", "ROI", "PnL real.", "Acierto", "Cerradas", "PF", "DD", "Hold med.", "Calidad", "Última op.", "Motivos"]} empty={!data?.length}>
          {(data ?? []).map((t) => (
            <tr key={t.address}>
              <td className="num text-slate-500">{t.rank}</td>
              <td><Link className="num text-accent hover:underline" to={`/traders/${t.address}`}>{t.label ?? short(t.address, 5)}</Link>{t.origin === "manual" && <Badge>manual</Badge>}</td>
              <td className="num">{t.score?.toFixed(1) ?? "—"}</td>
              <td><Badge tone={t.status === "followed" ? "info" : t.status === "candidate" ? "up" : "neutral"}>{t.status}</Badge></td>
              <td><Pnl v={t.roi}>{t.roi == null ? "—" : fmtPct(t.roi * 100, 1)}</Pnl></td>
              <td><Pnl v={t.realized_pnl}>{fmtUsd(t.realized_pnl, 0)}</Pnl></td>
              <td className="num">{t.win_rate == null ? "—" : `${(t.win_rate * 100).toFixed(0)}%`}</td>
              <td className="num">{t.n_closed ?? 0}</td>
              <td className="num">{t.profit_factor?.toFixed(2) ?? "—"}</td>
              <td className="num">{t.max_drawdown_pct == null ? "—" : `${t.max_drawdown_pct.toFixed(0)}%`}</td>
              <td className="num">{t.median_hold_hours == null ? "—" : `${(t.median_hold_hours * 60).toFixed(0)}m`}</td>
              <td className="num">{t.data_quality?.toFixed(2) ?? "—"}{t.verified ? " ✓" : ""}</td>
              <td>{ago(t.last_trade_at)}</td>
              <td className="max-w-xs truncate text-xs text-slate-500" title={(t.excluded_reasons ?? []).join("\n")}>{(t.excluded_reasons ?? []).join("; ")}</td>
            </tr>
          ))}
        </Table>
        <div className="mt-2 text-[11px] text-slate-500">Rentabilidades estimadas a partir de datos on-chain; "✓" solo cuando el historial es suficiente y con coste conocido. Transferencias y airdrops no cuentan como beneficio.</div>
      </Card>
    </div>
  );
}
