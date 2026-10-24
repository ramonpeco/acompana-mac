// Thin API client: JWT in localStorage, JSON helpers, WebSocket hook.
import { useEffect, useRef, useState } from "react";

const TOKEN_KEY = "ata_token";

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(t: string | null) {
  try {
    if (t) localStorage.setItem(TOKEN_KEY, t);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable */
  }
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

// ---- read-only demo mode: answers come from an embedded snapshot (no backend) ----
const DEMO: Record<string, any> | null = (() => {
  try {
    const el = typeof document !== "undefined" ? document.getElementById("demo-data") : null;
    return el ? JSON.parse(el.textContent || "{}") : null;
  } catch {
    return null;
  }
})();
export const isDemo = DEMO !== null;
export const demoSnapshotAt: string | null = DEMO?.["/dashboard"]?.heartbeat ?? null;

function demoAnswer(path: string, opts: { method?: string; body?: any }): any {
  const method = opts.method ?? (opts.body ? "POST" : "GET");
  if (path === "/backtest" && method === "POST") {
    const reg = opts.body?.regime ?? "neutral";
    const r = DEMO!.__backtest_by_regime?.[reg];
    if (!r) throw new ApiError(404, "Escenario no disponible en la demo");
    const keep = new Set<string>(opts.body?.strategies ?? Object.keys(r.results));
    return { ...r, results: Object.fromEntries(Object.entries<any>(r.results).filter(([k]) => keep.has(k))) };
  }
  if (method !== "GET") throw new ApiError(403, "Demo de solo lectura: esta acción requiere el servidor en marcha");
  if (path in DEMO!) return DEMO![path];
  const [base, qs] = path.split("?");
  const q = new URLSearchParams(qs ?? "");
  if (base === "/scanner") {
    const key = `/scanner?${new URLSearchParams({ category: q.get("category") ?? "trending", timeframe: q.get("timeframe") ?? "1h", chain: "solana" })}`;
    const r = DEMO![key];
    if (!r) throw new ApiError(404, "sin datos");
    const n = (k: string) => (q.get(k) ? Number(q.get(k)) : null);
    const rows = r.rows.filter((x: any) =>
      (n("min_liquidity") == null || (x.liquidity_usd ?? 0) >= n("min_liquidity")!) &&
      (n("min_volume") == null || (x.volume ?? 0) >= n("min_volume")!) &&
      (n("min_mcap") == null || (x.market_cap_usd ?? 0) >= n("min_mcap")!) &&
      (n("max_mcap") == null || (x.market_cap_usd ?? 0) <= n("max_mcap")!));
    return { ...r, rows, count: rows.length };
  }
  if (base === "/positions" || base === "/traders" || base === "/orders") {
    const alt = Object.keys(DEMO!).find((k) => k.startsWith(base + "?") && (!q.get("status") || k.includes(`status=${q.get("status")}`)));
    if (alt) return DEMO![alt];
  }
  throw new ApiError(404, "No incluido en la instantánea de la demo");
}

export async function api<T = any>(path: string, opts: { method?: string; body?: unknown } = {}): Promise<T> {
  if (DEMO) return demoAnswer(path, opts as any) as T;
  const res = await fetch(`/api${path}`, {
    method: opts.method ?? (opts.body ? "POST" : "GET"),
    headers: {
      "content-type": "application/json",
      ...(getToken() ? { authorization: `Bearer ${getToken()}` } : {}),
    },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 401) {
    setToken(null);
    window.dispatchEvent(new Event("ata-logout"));
  }
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch {
      /* not json */
    }
    throw new ApiError(res.status, msg);
  }
  return res.json();
}

/** Poll an endpoint; refetch on interval and when `deps` change. */
export function usePoll<T>(path: string | null, intervalMs = 10000, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!path) return;
    let alive = true;
    const load = async () => {
      setLoading(true);
      try {
        const d = await api<T>(path);
        if (alive) {
          setData(d);
          setError(null);
        }
      } catch (e: any) {
        if (alive) setError(e.message);
      } finally {
        if (alive) setLoading(false);
      }
    };
    load();
    const id = intervalMs > 0 ? setInterval(load, intervalMs) : undefined;
    return () => {
      alive = false;
      if (id) clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, intervalMs, tick, ...deps]);
  return { data, error, loading, reload: () => setTick((t) => t + 1) };
}

export type LiveEvent = { type: string; ts?: string; level?: string; kind?: string; message?: string; data?: any; equity?: number };

/** Live agent events over WebSocket with auto-reconnect. */
export function useLiveEvents(max = 50) {
  const [events, setEvents] = useState<LiveEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  useEffect(() => {
    let stop = false;
    let retry: ReturnType<typeof setTimeout>;
    if (DEMO) {
      setEvents((DEMO["/events?limit=100"] ?? []).slice(0, max).map((e: any) => ({ ...e, type: "event" })));
      return;
    }
    const connect = () => {
      const t = getToken();
      if (!t || stop) return;
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${proto}://${location.host}/api/ws?token=${encodeURIComponent(t)}`);
      wsRef.current = ws;
      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        if (!stop) retry = setTimeout(connect, 3000);
      };
      ws.onmessage = (m) => {
        const msg: LiveEvent = JSON.parse(m.data);
        if (msg.type === "event") setEvents((e) => [msg, ...e].slice(0, max));
      };
    };
    connect();
    return () => {
      stop = true;
      clearTimeout(retry);
      wsRef.current?.close();
    };
  }, [max]);
  return { events, connected };
}

export const fmtUsd = (v: number | null | undefined, d = 2) =>
  v == null || Number.isNaN(v) ? "—" : `${v < 0 ? "-" : ""}$${Math.abs(v).toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: d })}`;
export const fmtPct = (v: number | null | undefined, d = 2) => (v == null || Number.isNaN(v) ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(d)}%`);
export const fmtNum = (v: number | null | undefined, d = 2) =>
  v == null || Number.isNaN(v) ? "—" : Math.abs(v) >= 1e6 ? `${(v / 1e6).toFixed(2)}M` : Math.abs(v) >= 1e3 ? `${(v / 1e3).toFixed(1)}k` : v.toFixed(d);
export const fmtPrice = (v: number | null | undefined) => (v == null ? "—" : v >= 1 ? v.toFixed(4) : v.toPrecision(4));
export const short = (a?: string | null, n = 4) => (a ? `${a.slice(0, n)}…${a.slice(-n)}` : "—");
export const ago = (iso?: string | null) => {
  if (!iso) return "—";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return `${Math.max(0, Math.round(s))}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  if (s < 86400) return `${(s / 3600).toFixed(1)}h`;
  return `${(s / 86400).toFixed(1)}d`;
};

/** In-page notices (the artifact viewer blocks alert/confirm). */
export function notify(msg: string) {
  window.dispatchEvent(new CustomEvent("ata-notice", { detail: msg }));
}
export function ask(msg: string): boolean {
  if (isDemo) {
    notify("Demo de solo lectura: esta acción requiere el servidor en marcha");
    return false;
  }
  return window.confirm(msg);
}
