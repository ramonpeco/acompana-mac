import { useState } from "react";
import { api, ago, fmtNum, fmtPct, fmtPrice, fmtUsd, short, usePoll } from "../api";
import { Badge, Btn, Card, ErrorBox, Field, inputCls, Pnl, Table } from "../components/ui";

const CATS = [
  ["trending", "Trending"], ["gainers", "Gainers"], ["top", "Top"], ["new_pairs", "New pairs"],
  ["graduated", "Graduated"], ["dex_paid", "DEX paid"], ["high_volume", "High volume"], ["smart_money", "Smart money"],
] as const;
const TFS = ["1m", "5m", "15m", "1h", "4h", "24h", "7d"];
const CHAINS = [["solana", "Solana"], ["ethereum", "Ethereum (pendiente)"], ["base", "Base (pendiente)"], ["bsc", "BNB Chain (pendiente)"], ["arbitrum", "Arbitrum (pendiente)"]];

export default function Scanner() {
  const [cat, setCat] = useState("trending");
  const [tf, setTf] = useState("1h");
  const [chain, setChain] = useState("solana");
  const [f, setF] = useState({ min_liquidity: "", min_volume: "", min_mcap: "", max_mcap: "" });
  const [sel, setSel] = useState<any>(null);
  const qs = new URLSearchParams({ category: cat, timeframe: tf, chain, ...Object.fromEntries(Object.entries(f).filter(([, v]) => v)) });
  const { data, error, loading } = usePoll<any>(`/scanner?${qs}`, 20000);
  const open = async (addr: string) => setSel(await api(`/tokens/${addr}`));
  const assess = async (addr: string) => {
    await api(`/tokens/${addr}/assess`, { method: "POST" });
    open(addr);
  };
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-1">
        {CATS.map(([k, l]) => (
          <button key={k} onClick={() => setCat(k)} className={`rounded border px-3 py-1 text-sm ${cat === k ? "border-accent text-accent" : "border-edge text-slate-400"}`}>{l}</button>
        ))}
      </div>
      <Card>
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Red">
            <select className={inputCls} value={chain} onChange={(e) => setChain(e.target.value)}>
              {CHAINS.map(([k, l]) => <option key={k} value={k} disabled={k !== "solana"}>{l}</option>)}
            </select>
          </Field>
          <Field label="Intervalo">
            <div className="flex gap-1">
              {TFS.map((t) => <button key={t} onClick={() => setTf(t)} className={`rounded border px-2 py-1 text-xs ${tf === t ? "border-accent text-accent" : "border-edge text-slate-400"}`}>{t}</button>)}
            </div>
          </Field>
          {(["min_liquidity", "min_volume", "min_mcap", "max_mcap"] as const).map((k) => (
            <Field key={k} label={{ min_liquidity: "Liquidez mín. $", min_volume: "Volumen mín. $", min_mcap: "Mcap mín. $", max_mcap: "Mcap máx. $" }[k]}>
              <input className={`${inputCls} w-28`} inputMode="numeric" value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} />
            </Field>
          ))}
          <span className="text-xs text-slate-500">{loading ? "actualizando…" : `${data?.count ?? 0} tokens · refresco ${ago(data?.last_refresh)}`}</span>
        </div>
        {data?.timeframe_is_approximation && (
          <div className="mt-2 text-xs text-accent">
            Intervalo {tf}: las fuentes no lo ofrecen directamente; se usa {data.window_used} como aproximación (marcado como estimado).
          </div>
        )}
      </Card>
      <ErrorBox error={error} />
      <Card title={`${CATS.find((c) => c[0] === cat)?.[1]} · ${tf}`}>
        <Table head={["Token", "Precio", `Δ ${tf}`, "Mcap", "FDV", "Liquidez", `Vol ${tf}`, "Compras/Ventas 1h", "Edad", "Riesgo", "Flags", "Datos"]} empty={!data?.rows?.length}>
          {(data?.rows ?? []).map((r: any) => (
            <tr key={r.address} className="cursor-pointer" onClick={() => open(r.address)}>
              <td><div className="font-medium">{r.symbol ?? "?"}</div><div className="num text-[11px] text-slate-500">{short(r.address, 5)}</div></td>
              <td className="num">{fmtPrice(r.price_usd)}</td>
              <td><Pnl v={r.change_pct}>{fmtPct(r.change_pct)}</Pnl></td>
              <td className="num">{fmtNum(r.market_cap_usd)}</td>
              <td className="num">{fmtNum(r.fdv_usd)}</td>
              <td className="num">{fmtNum(r.liquidity_usd)}</td>
              <td className="num">{fmtNum(r.volume)}</td>
              <td className="num">{r.txns?.h1 ? `${r.txns.h1.buys ?? "?"}/${r.txns.h1.sells ?? "?"}` : "—"}</td>
              <td className="num">{r.age_hours == null ? "—" : r.age_hours < 48 ? `${r.age_hours.toFixed(1)}h` : `${(r.age_hours / 24).toFixed(0)}d`}</td>
              <td>{r.risk_score == null ? <Badge>sin evaluar</Badge> : <Badge tone={r.risk_score < 30 ? "up" : r.risk_score < 60 ? "warn" : "down"}>{r.risk_score} {r.risk_level}</Badge>}</td>
              <td className="space-x-1">
                {r.graduated && <Badge tone="info">grad</Badge>}
                {r.dex_paid && <Badge tone="warn">paid</Badge>}
                {r.smart_wallets ? <Badge tone="up">{r.smart_wallets} smart</Badge> : null}
                {r.boosts_active ? <Badge>boost</Badge> : null}
              </td>
              <td>{r.stale ? <Badge tone="warn">obsoleto</Badge> : <span className="text-[11px] text-slate-500">{Object.values(r.sources ?? {}).join(",")}</span>}</td>
            </tr>
          ))}
        </Table>
        <div className="mt-2 text-[11px] text-slate-500">
          "DEX paid" indica un pago de perfil/servicio en DexScreener: no es garantía de legitimidad. "—" = dato desconocido (nunca se inventa).
        </div>
      </Card>
      {sel && (
        <Card title={`${sel.symbol ?? "?"} · ${sel.address}`} right={<div className="flex gap-2"><Btn onClick={() => assess(sel.address)}>Evaluar riesgo</Btn><Btn onClick={() => setSel(null)}>Cerrar</Btn></div>}>
          <div className="grid gap-3 md:grid-cols-3">
            <div className="space-y-1 text-sm">
              <div>Precio <span className="num">{fmtPrice(sel.market?.price_usd)}</span></div>
              <div>Mcap <span className="num">{fmtUsd(sel.market?.market_cap_usd, 0)}</span> · FDV <span className="num">{fmtUsd(sel.market?.fdv_usd, 0)}</span></div>
              <div>Liquidez <span className="num">{fmtUsd(sel.market?.liquidity_usd, 0)}</span></div>
              <div>Holders <span className="num">{sel.market?.holders ?? "desconocido"}</span></div>
              <div>Pool creado {sel.pool_created_at ?? "desconocido"} · DEX {sel.market?.dex_id}</div>
              <div>Estimados: {(sel.market?.estimated ?? []).join(", ") || "ninguno"}</div>
              {sel.market?.url && <a className="text-accent hover:underline" href={sel.market.url} target="_blank" rel="noreferrer">ver en DexScreener</a>}
            </div>
            <div className="text-sm md:col-span-2">
              {sel.risk?.score != null ? (
                <>
                  <div className="mb-1">Riesgo <b>{sel.risk.score}</b> ({sel.risk.level}) · venta: {sel.risk.sellability}</div>
                  <ul className="list-disc space-y-0.5 pl-5 text-slate-300">
                    {sel.risk.factors.map((x: any) => <li key={x.code}>+{x.points} {x.detail}</li>)}
                  </ul>
                  {sel.risk.unknowns?.length > 0 && <div className="mt-1 text-accent">Desconocido: {sel.risk.unknowns.join(", ")}</div>}
                  <div className="mt-1 text-xs text-slate-500">{sel.risk.disclaimer}</div>
                </>
              ) : <div className="text-slate-500">Riesgo no evaluado todavía.</div>}
              {sel.tracked_wallet_buyers?.length > 0 && <div className="mt-2">Wallets seguidas que lo compraron: {sel.tracked_wallet_buyers.length}</div>}
            </div>
          </div>
        </Card>
      )}
    </div>
  );
}
