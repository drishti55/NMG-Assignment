import { useState } from "react";
import { BarList, Card, Empty, ErrorBox, Loading, PageHeader, Stat } from "../components/ui";
import { api, fmt, useApi } from "../lib/api";

interface Summary {
  eval_id: string;
  created_at: string;
  config: Record<string, unknown>;
  per_run: { action_accuracy: number; category_accuracy: number; priority_accuracy: number; injection_recall: number | null; injection_false_positive_rate: number | null; tokens_in: number; tokens_out: number; failed: number }[];
  aggregate: { runs: number; tickets: number; action_accuracy: number; category_accuracy: number; priority_accuracy: number; injection_recall: number | null; injection_false_positive_rate: number | null; stability: number; unstable_tickets: string[]; tokens_in_total: number; tokens_out_total: number };
  wall_time_s: { per_run: number[]; total: number };
  per_ticket_errors: { ticket_id: string; runs: number; mismatches: Record<string, { expected: string; got: Record<string, number> }> }[];
  exceptions: { run: number; ticket_id: string; error: string }[];
}

export default function Evaluation() {
  const ev = useApi<{ running: string | null; runs: { eval_id: string; summary: Summary }[]; files: string[] }>("/api/evaluations", 10000);
  const [pick, setPick] = useState<string | null>(null);
  const [file, setFile] = useState<string | null>(null);
  const rows = useApi<Record<string, unknown>[]>(file ? `/api/evaluations/files/${file}` : null);
  const [startErr, setStartErr] = useState<string>();

  if (ev.error) return <ErrorBox error={ev.error} onRetry={ev.reload} />;
  if (!ev.data) return <Loading rows={6} />;
  const s = (ev.data.runs.find((r) => r.eval_id === pick) ?? ev.data.runs[0])?.summary;

  const start = async () => {
    setStartErr(undefined);
    try {
      await api("/api/evaluations/run", { method: "POST", body: JSON.stringify({ runs: 5 }) });
      ev.reload();
    } catch (e) {
      setStartErr((e as Error).message);
    }
  };

  return (
    <>
      <PageHeader
        title="Evaluation lab"
        subtitle="Golden-set evaluation of the production graph (temperature 0, fixed seed). These are offline measurements, separate from production decisions; golden labels never enter the decision path."
        actions={<button className="btn-primary" disabled={!!ev.data.running} onClick={start}>{ev.data.running ? "Evaluation running…" : "Run 5× evaluation"}</button>}
      />
      {startErr && <div className="mb-4"><ErrorBox error={startErr} /></div>}
      {!s ? <Empty title="No evaluation recorded yet">Run <code className="mono">make eval RUNS=5</code>.</Empty> : (
        <>
          {ev.data.runs.length > 1 && (
            <select className="input mb-4" value={s.eval_id} onChange={(e) => setPick(e.target.value)}>
              {ev.data.runs.map((r) => <option key={r.eval_id} value={r.eval_id}>{r.eval_id}</option>)}
            </select>
          )}
          <p className="mb-3 text-sm text-fg-muted">
            <span className="mono">{s.eval_id}</span> · {s.aggregate.runs} runs × {s.aggregate.tickets} tickets · {String(s.config.triage_model)}
            {s.config.drafting ? ` + ${String(s.config.draft_model)}` : " (no drafting)"} · seed {String(s.config.seed)}
          </p>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Action accuracy" value={fmt.pct(s.aggregate.action_accuracy)} />
            <Stat label="Category accuracy" value={fmt.pct(s.aggregate.category_accuracy)} />
            <Stat label="Injection recall" value={fmt.pct(s.aggregate.injection_recall)} hint={`false-positive rate ${fmt.pct(s.aggregate.injection_false_positive_rate)}`} />
            <Stat label="Stability" value={fmt.pct(s.aggregate.stability)} hint="same action in every run" />
            <Stat label="Priority accuracy" value={fmt.pct(s.aggregate.priority_accuracy)} />
            <Stat label="Tokens in" value={fmt.int(s.aggregate.tokens_in_total)} />
            <Stat label="Tokens out" value={fmt.int(s.aggregate.tokens_out_total)} />
            <Stat label="Wall time" value={`${s.wall_time_s.total.toFixed(0)}s`} hint={`${(s.wall_time_s.total / s.aggregate.runs).toFixed(0)}s per run`} />
          </div>

          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <Card title="Action accuracy by run">
              <BarList items={s.per_run.map((r, i) => ({ label: `run ${i + 1}`, value: r.action_accuracy, hint: `${s.wall_time_s.per_run[i]}s` }))} format={(n) => fmt.pct(n)} />
            </Card>
            <Card title="Category accuracy by run">
              <BarList items={s.per_run.map((r, i) => ({ label: `run ${i + 1}`, value: r.category_accuracy }))} format={(n) => fmt.pct(n)} />
            </Card>
          </div>

          <Card title="Errors per ticket" className="mt-4">
            {!s.per_ticket_errors.length ? <p className="text-sm text-fg-faint">No mismatches.</p> : (
              <table className="w-full text-sm">
                <thead className="text-left text-fg-faint"><tr>{["Ticket", "Field", "Expected", "Got (runs)"].map((h) => <th key={h} className="pb-2 font-medium">{h}</th>)}</tr></thead>
                <tbody className="divide-y divide-ink-800">
                  {s.per_ticket_errors.flatMap((e) => Object.entries(e.mismatches).map(([field, m]) => (
                    <tr key={e.ticket_id + field}>
                      <td className="mono py-1.5">{e.ticket_id}</td>
                      <td className="text-fg-muted">{fmt.label(field)}</td>
                      <td>{fmt.label(m.expected)}</td>
                      <td>{Object.entries(m.got).map(([v, n]) => `${fmt.label(v)} (${n}/${e.runs})`).join(", ")}</td>
                    </tr>
                  )))}
                </tbody>
              </table>
            )}
            <p className="mt-3 text-xs text-fg-faint">Exceptions: {s.exceptions.length ? s.exceptions.map((x) => `run ${x.run} ${x.ticket_id}: ${x.error}`).join("; ") : "none"}</p>
          </Card>

          <Card title="Raw run records" className="mt-4" right={<span className="text-xs text-fg-faint">eval/runs/*.jsonl</span>}>
            <div className="mb-3 flex flex-wrap gap-2">
              {ev.data.files.map((f) => <button key={f} className={file === f ? "btn-primary" : "btn-ghost"} onClick={() => setFile(f)}>{f}</button>)}
            </div>
            {file && (!rows.data ? <Loading /> : (
              <div className="max-h-96 overflow-auto">
                <table className="w-full text-sm">
                  <thead className="sticky top-0 bg-ink-900 text-left text-fg-faint"><tr>{["ticket", "category", "priority", "action", "injection", "status", "tokens", "latency"].map((h) => <th key={h} className="pb-2 font-medium">{h}</th>)}</tr></thead>
                  <tbody className="divide-y divide-ink-800 tabular-nums">
                    {rows.data.map((r) => (
                      <tr key={String(r.ticket_id)}>
                        <td className="mono py-1">{String(r.ticket_id)}</td>
                        <td>{fmt.label(String(r.category))}</td>
                        <td>{String(r.priority)}</td>
                        <td>{fmt.label(String(r.action))}</td>
                        <td>{r.injection_detected ? "yes" : "no"}</td>
                        <td>{fmt.label(String(r.status))}</td>
                        <td>{fmt.int(Number(r.tokens_in))}/{fmt.int(Number(r.tokens_out))}</td>
                        <td>{fmt.ms(Number(r.latency_ms))}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
          </Card>
        </>
      )}
    </>
  );
}
