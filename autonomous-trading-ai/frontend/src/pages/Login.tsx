import { useState } from "react";
import { api, setToken } from "../api";
import { Btn, ErrorBox, Field, inputCls } from "../components/ui";

export default function Login({ onLogin }: { onLogin: () => void }) {
  const [username, setU] = useState("admin");
  const [password, setP] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      const r = await api<{ token: string }>("/auth/login", { body: { username, password } });
      setToken(r.token);
      onLogin();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="flex min-h-full items-center justify-center p-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-md border border-edge bg-panel p-6">
        <div>
          <div className="text-lg font-semibold text-accent">AUTONOMOUS TRADING AI</div>
          <div className="text-xs text-slate-500">Panel de investigación · paper trading</div>
        </div>
        <Field label="Usuario">
          <input className={inputCls} value={username} onChange={(e) => setU(e.target.value)} autoComplete="username" />
        </Field>
        <Field label="Contraseña">
          <input className={inputCls} type="password" value={password} onChange={(e) => setP(e.target.value)} autoComplete="current-password" />
        </Field>
        <ErrorBox error={error} />
        <Btn type="submit" kind="primary" disabled={busy}>{busy ? "Entrando…" : "Entrar"}</Btn>
      </form>
    </div>
  );
}
