import type { ReactNode } from "react";

export function Card({ title, children, right, className = "" }: { title?: ReactNode; children: ReactNode; right?: ReactNode; className?: string }) {
  return (
    <section className={`rounded-md border border-edge bg-panel ${className}`}>
      {title && (
        <header className="flex items-center justify-between border-b border-edge px-3 py-2 text-xs uppercase tracking-wider text-slate-400">
          <span>{title}</span>
          {right}
        </header>
      )}
      <div className="p-3">{children}</div>
    </section>
  );
}

export function Stat({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: "up" | "down" | "warn" | null }) {
  const color = tone === "up" ? "text-up" : tone === "down" ? "text-down" : tone === "warn" ? "text-accent" : "text-slate-100";
  return (
    <div className="rounded-md border border-edge bg-panel px-3 py-2">
      <div className="text-[11px] uppercase tracking-wider text-slate-500">{label}</div>
      <div className={`num text-lg ${color}`}>{value}</div>
      {sub && <div className="num text-xs text-slate-500">{sub}</div>}
    </div>
  );
}

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: "up" | "down" | "warn" | "neutral" | "info" }) {
  const c = {
    up: "border-up/40 text-up bg-up/10",
    down: "border-down/40 text-down bg-down/10",
    warn: "border-accent/40 text-accent bg-accent/10",
    info: "border-sky-500/40 text-sky-400 bg-sky-500/10",
    neutral: "border-edge text-slate-400 bg-white/5",
  }[tone];
  return <span className={`inline-block rounded border px-1.5 py-0.5 text-[11px] ${c}`}>{children}</span>;
}

export function tone(v: number | null | undefined): "up" | "down" | null {
  if (v == null || v === 0) return null;
  return v > 0 ? "up" : "down";
}

export function Pnl({ v, children }: { v: number | null | undefined; children: ReactNode }) {
  const t = tone(v);
  return <span className={`num ${t === "up" ? "text-up" : t === "down" ? "text-down" : ""}`}>{children}</span>;
}

export function Btn({ children, onClick, kind = "default", disabled, type = "button" }: {
  children: ReactNode; onClick?: () => void; kind?: "default" | "danger" | "primary"; disabled?: boolean; type?: "button" | "submit";
}) {
  const c = {
    default: "border-edge bg-white/5 hover:bg-white/10 text-slate-200",
    primary: "border-accent/60 bg-accent/15 hover:bg-accent/25 text-accent",
    danger: "border-down/60 bg-down/15 hover:bg-down/30 text-down",
  }[kind];
  return (
    <button type={type} disabled={disabled} onClick={onClick} className={`rounded border px-3 py-1.5 text-sm disabled:opacity-40 ${c}`}>
      {children}
    </button>
  );
}

export function Table({ head, children, empty }: { head: ReactNode[]; children: ReactNode; empty?: boolean }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-edge text-left text-[11px] uppercase tracking-wider text-slate-500">
            {head.map((h, i) => (
              <th key={i} className="whitespace-nowrap px-2 py-1.5 font-normal">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody className="[&>tr]:border-b [&>tr]:border-edge/60 [&>tr:hover]:bg-white/[0.03] [&_td]:px-2 [&_td]:py-1.5 [&_td]:whitespace-nowrap">
          {children}
        </tbody>
      </table>
      {empty && <div className="py-6 text-center text-sm text-slate-500">Sin datos todavía</div>}
    </div>
  );
}

export function ErrorBox({ error }: { error: string | null }) {
  if (!error) return null;
  return <div className="rounded border border-down/40 bg-down/10 px-3 py-2 text-sm text-down">{error}</div>;
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-1 text-xs text-slate-400">
      {label}
      {children}
    </label>
  );
}

export const inputCls = "rounded border border-edge bg-ink px-2 py-1.5 text-sm text-slate-100 outline-none focus:border-accent/60";
