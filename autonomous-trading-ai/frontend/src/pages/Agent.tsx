import { api, ago, usePoll } from "../api";
import { Badge, Btn, Card, ErrorBox, Table } from "../components/ui";

export default function AgentPage() {
  const { data, error, reload } = usePoll<any>("/ai", 20000);
  const { data: dash } = usePoll<any>("/dashboard", 15000);
  const ctl = async (a: string) => {
    try {
      await api(`/agent/${a}`, { method: "POST" });
    } catch (e: any) {
      alert(e.message);
    }
    reload();
  };
  const act = async (id: number, action: string) => {
    try {
      await api(`/strategies/${id}/${action}`, { method: "POST" });
    } catch (e: any) {
      alert(e.message);
    }
    reload();
  };
  const reports = (data?.decisions ?? []).filter((d: any) => d.kind === "daily_report");
  return (
    <div className="space-y-3">
      <ErrorBox error={error} />
      <div className="grid gap-3 xl:grid-cols-3">
        <Card title="Estado del agente">
          <div className="space-y-1 text-sm">
            <div>Bucle: <b className={dash?.agent?.state === "running" ? "text-up" : "text-down"}>{dash?.agent?.state}</b> · último ciclo {ago(dash?.agent?.last_tick)}</div>
            <div>Proveedor IA: <b>{data?.provider}</b> {data?.model && <span className="num text-slate-500">({data.model})</span>} {!data?.available && <Badge tone="warn">reglas predefinidas</Badge>}</div>
            <div className="flex flex-wrap gap-2 pt-2">
              <Btn onClick={() => ctl(dash?.paused ? "resume" : "pause")}>{dash?.paused ? "Reanudar" : "Pausar entradas"}</Btn>
              <Btn onClick={() => ctl("tick")}>Ejecutar ciclo</Btn>
              <Btn onClick={() => ctl(dash?.agent?.state === "running" ? "stop" : "start")}>{dash?.agent?.state === "running" ? "Detener bucle" : "Iniciar bucle"}</Btn>
            </div>
          </div>
        </Card>
        <Card title="Salvaguardas" className="xl:col-span-2">
          <ul className="list-disc space-y-0.5 pl-5 text-sm text-slate-300">
            {(data?.guardrails ?? []).map((g: string) => <li key={g}>{g}</li>)}
            <li>Si la IA no está disponible, el agente sigue con reglas deterministas.</li>
          </ul>
        </Card>
      </div>

      <Card title="Decisiones y justificaciones">
        <div className="space-y-2">
          {(data?.decisions ?? []).filter((d: any) => d.kind !== "daily_report").map((d: any) => (
            <div key={d.id} className="border-b border-edge/60 pb-2 text-sm">
              <div className="flex items-center gap-2">
                <span className="num text-xs text-slate-500">{d.ts?.slice(0, 19).replace("T", " ")}</span>
                <Badge tone={d.level === "alert" ? "down" : d.level === "warn" ? "warn" : "info"}>{d.kind}</Badge>
              </div>
              <div className="mt-0.5 text-slate-300">{d.message}</div>
            </div>
          ))}
          {!data?.decisions?.length && <div className="text-sm text-slate-500">Sin decisiones todavía.</div>}
        </div>
      </Card>

      <div className="grid gap-3 xl:grid-cols-2">
        <Card title="Informes diarios">
          {reports.length === 0 && <div className="text-sm text-slate-500">El primer informe se genera al cambiar el día.</div>}
          {reports.map((r: any) => (
            <div key={r.id} className="mb-3 text-sm">
              <div className="text-xs text-slate-500">{r.ts?.slice(0, 10)} · {r.data?.report?.source}</div>
              <div className="whitespace-pre-wrap text-slate-300">{r.message}</div>
              {(r.data?.report?.alerts ?? []).map((a: string) => <div key={a} className="text-accent">⚠ {a}</div>)}
            </div>
          ))}
        </Card>
        <Card title="Últimos análisis de IA">
          <Table head={["Hora", "Tipo", "Proveedor", "OK", "Resumen"]} empty={!data?.analyses?.length}>
            {(data?.analyses ?? []).map((a: any) => (
              <tr key={a.id}>
                <td>{ago(a.ts)}</td><td>{a.kind}</td><td>{a.provider}</td>
                <td>{a.ok ? <Badge tone="up">ok</Badge> : <Badge tone="down" >error</Badge>}</td>
                <td className="max-w-md truncate text-xs text-slate-400" title={a.error ?? JSON.stringify(a.output)}>{a.error ?? a.output?.summary ?? a.output?.text ?? a.output?.title}</td>
              </tr>
            ))}
          </Table>
        </Card>
      </div>

      <Card title="Propuestas de estrategia (versionadas)">
        <Table head={["ID", "Nombre", "Estado", "Propuesto por", "Cambios", "Validación OOS", "Acciones"]} empty={!data?.proposals?.length}>
          {(data?.proposals ?? []).map((p: any) => (
            <tr key={p.id}>
              <td className="num">{p.id}</td><td>{p.name}</td><td><Badge tone={p.status === "active" ? "up" : p.status === "rejected" ? "down" : "warn"}>{p.status}</Badge></td>
              <td className="text-xs">{p.proposed_by}</td>
              <td className="max-w-xs truncate text-xs" title={JSON.stringify(p.params)}>{(p.params?.changes ?? []).map((c: any) => `${c.section}.${c.key}=${c.value}`).join(", ")}</td>
              <td className="text-xs">{p.oos_results?.proposal ? `OOS ${p.oos_results.proposal.mean_oos_return_pct?.toFixed(2)}% vs ${p.oos_results.baseline.mean_oos_return_pct?.toFixed(2)}%` : "pendiente"}</td>
              <td className="space-x-1">
                {p.status === "proposed" && <><Btn onClick={() => act(p.id, "validate")}>Validar</Btn><Btn onClick={() => act(p.id, "approve")}>Aprobar</Btn><Btn kind="danger" onClick={() => act(p.id, "reject")}>Rechazar</Btn></>}
                {p.status === "approved" && <Btn kind="primary" onClick={() => act(p.id, "activate")}>Activar</Btn>}
              </td>
            </tr>
          ))}
        </Table>
      </Card>
    </div>
  );
}
