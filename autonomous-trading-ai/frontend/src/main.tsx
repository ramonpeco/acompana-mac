import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, NavLink, Route, Routes } from "react-router-dom";
import "./index.css";
import { getToken, setToken, usePoll } from "./api";
import { Badge } from "./components/ui";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Scanner from "./pages/Scanner";
import Traders from "./pages/Traders";
import TraderDetail from "./pages/TraderDetail";
import CopyTrading from "./pages/CopyTrading";
import AgentPage from "./pages/Agent";
import Risk from "./pages/Risk";
import Backtest from "./pages/Backtest";

const NAV = [
  ["/", "Dashboard"],
  ["/scanner", "Token Scanner"],
  ["/traders", "Trader Explorer"],
  ["/copy", "Copy Trading"],
  ["/agent", "AI Agent"],
  ["/risk", "Risk Control"],
  ["/backtest", "Backtesting"],
] as const;

function Shell() {
  const { data } = usePoll<any>("/dashboard", 15000);
  const [open, setOpen] = useState(false);
  const synthetic = data?.data_label === "synthetic";
  return (
    <div className="flex min-h-full flex-col">
      {synthetic && (
        <div className="bg-accent/90 px-4 py-1 text-center text-xs font-semibold text-black">
          MODO SANDBOX · DATOS SINTÉTICOS · los resultados no representan el mercado real
        </div>
      )}
      <header className="flex items-center justify-between border-b border-edge bg-panel px-4 py-2">
        <div className="flex items-center gap-3">
          <button className="text-slate-400 md:hidden" onClick={() => setOpen(!open)} aria-label="menú">☰</button>
          <span className="font-semibold tracking-wide text-accent">AUTONOMOUS TRADING AI</span>
          <Badge tone="info">PAPER</Badge>
          {data?.kill_switch && <Badge tone="down">KILL SWITCH</Badge>}
          {data?.paused && !data?.kill_switch && <Badge tone="warn">PAUSADO</Badge>}
        </div>
        <div className="flex items-center gap-3 text-xs text-slate-400">
          <span className="hidden sm:inline">agente: <b className={data?.agent?.state === "running" ? "text-up" : "text-down"}>{data?.agent?.state ?? "…"}</b></span>
          <button className="hover:text-slate-200" onClick={() => { setToken(null); location.href = "/"; }}>salir</button>
        </div>
      </header>
      <div className="flex flex-1">
        <nav className={`${open ? "block" : "hidden"} w-48 shrink-0 border-r border-edge bg-panel py-2 md:block`}>
          {NAV.map(([to, label]) => (
            <NavLink key={to} to={to} end={to === "/"} onClick={() => setOpen(false)}
              className={({ isActive }) => `block px-4 py-2 text-sm ${isActive ? "border-l-2 border-accent bg-white/5 text-accent" : "text-slate-400 hover:text-slate-200"}`}>
              {label}
            </NavLink>
          ))}
        </nav>
        <main className="min-w-0 flex-1 p-3 md:p-4">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/scanner" element={<Scanner />} />
            <Route path="/traders" element={<Traders />} />
            <Route path="/traders/:address" element={<TraderDetail />} />
            <Route path="/copy" element={<CopyTrading />} />
            <Route path="/agent" element={<AgentPage />} />
            <Route path="/risk" element={<Risk />} />
            <Route path="/backtest" element={<Backtest />} />
          </Routes>
        </main>
      </div>
      <footer className="border-t border-edge px-4 py-1.5 text-center text-[11px] text-slate-600">
        Herramienta de investigación con capital simulado. No es asesoramiento financiero ni garantiza rentabilidad.
      </footer>
    </div>
  );
}

function App() {
  const [authed, setAuthed] = useState(!!getToken());
  useEffect(() => {
    const h = () => setAuthed(false);
    window.addEventListener("ata-logout", h);
    return () => window.removeEventListener("ata-logout", h);
  }, []);
  return <BrowserRouter>{authed ? <Shell /> : <Login onLogin={() => setAuthed(true)} />}</BrowserRouter>;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
