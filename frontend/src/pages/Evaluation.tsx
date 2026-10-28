import { useState } from "react";
import { Card, ConfirmDialog, DevPanel, Empty, ErrorBox, Loading, Notice, PageHeader, Stat } from "../components/ui";
import { explainError, fmt, post, useApi } from "../lib/api";
import { useMode } from "../lib/mode";
import { ACTION, CATEGORY } from "../lib/status";

interface Summary {
  eval_id: string; created_at: string; config: Record<string, unknown>;
  per_run: { action_accuracy: number; category_accuracy: number; tokens_in: number; tokens_out: number }[];
  aggregate: {
    runs: number; tickets: number; action_accuracy: number; category_accuracy: number; priority_accuracy: number;
    injection_recall: number | null; injection_false_positive_rate: number | null; stability: number;
    unstable_tickets: string[]; tokens_in_total: number; tokens_out_total: number;
  };
  wall_time_s: { per_run: number[]; total: number };
  per_ticket_errors: { ticket_id: string; runs: number; mismatches: Record<string, { expected: string; got: Record<string, number> }> }[];
  exceptions: { run: number; ticket_id: string; error: string }[];
}

const FIELD: Record<string, string> = { action: "Decision", category: "Topic", priority: "Priority", injection_detected: "Manipulation flag" };
const valueLabel = (field: string, v: string) =>
  field === "action" ? ACTION[v]?.label ?? v : field === "category" ? CATEGORY[v] ?? v : field === "injection_detected" ? (v === "True" ? "flagged" : "not flagged") : v;

export default function Evaluation() {
  const ev = useApi<{ running: string | null; runs: { eval_id: string; summary: Summary }[]; files: string[] }>("/api/evaluations", 15000);
  const { isDev } = useMode();
  const [pick, setPick] = useState<string | null>(null);
  const [file, setFile] = useState<string | null>(null);
  const rows = useApi<Record<string, unknown>[]>(isDev && file ? `/api/evaluations/files/${file}` : null);
  const [confirm, setConfirm] = useState(false);
  const [msg, setMsg] = useState<{ tone: "good" | "critical"; text: string } | null>(null);

  if (ev.error) return <ErrorBox error={ev.error} onRetry={ev.reload} />;
  if (!ev.data) return <Loading />;
  const s = (pick ? ev.data.runs.find((r) => r.eval_id === pick) : ev.data.runs[0])?.summary;

  async function start() {
    try {
      await post("/api/evaluations/run", { runs: 5 });
      setMsg({ tone: "good", text: "Evaluation started. It takes about 10 minutes; results appear here when it finishes." });
      ev.reload();
    } catch (e) {
      setMsg({ tone: "critical", text: explainError(e) });
    } finally {
      setConfirm(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Evaluation"
        purpose="How often the system makes the same decision as a human-labelled answer key, measured on the 24 sample tickets. This is a test of the system, separate from real ticket handling: it never sends or approves anything."
        next="Read the scores and the list of mistakes. Re-run the evaluation after changing prompts or rules."
        actions={<button className="btn-primary" disabled={!!ev.data.running} onClick={() => setConfirm(true)}>{ev.data.running ? "Evaluation running…" : "Run the evaluation (5 rounds)"}</button>}
      />
      {msg && <div className="mb-4"><Notice tone={msg.tone}>{msg.text}</Notice></div>}
      {!s ? <Empty title="No evaluation recorded yet">Click “Run the evaluation” or run <code className="mono">make eval RUNS=5</code>.</Empty> : (
        <>
          <p className="mb-4 text-sm text-fg-muted">
            Latest result from {fmt.datetime(s.created_at)}: {s.aggregate.runs} rounds over {s.aggregate.tickets} tickets, same settings each round.
          </p>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Correct decision" value={fmt.pct(s.aggregate.action_accuracy)} help="Reply / ask for details / hand to a person matched the answer key." />
            <Stat label="Correct topic" value={fmt.pct(s.aggregate.category_accuracy)} help="The ticket's topic matched the answer key." />
            <Stat label="Manipulation attempts caught" value={fmt.pct(s.aggregate.injection_recall)} help={`Share of known attempts that were flagged. False alarms on normal tickets: ${fmt.pct(s.aggregate.injection_false_positive_rate)}.`} />
            <Stat label="Consistency" value={fmt.pct(s.aggregate.stability)} help="Tickets that got the same decision in every round." />
          </div>

          <Card title="Where it disagreed with the answer key" className="mt-5">
            {!s.per_ticket_errors.length ? <p className="text-sm text-fg-muted">No disagreements.</p> : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-left text-fg-faint"><tr><th className="pb-2 font-medium">Ticket</th><th className="pb-2 font-medium">What</th><th className="pb-2 font-medium">Expected</th><th className="pb-2 font-medium">System said (rounds)</th></tr></thead>
                  <tbody className="divide-y divide-ink-800">
                    {s.per_ticket_errors.flatMap((e) => Object.entries(e.mismatches)
                      .filter(([f]) => isDev || f !== "priority")
                      .map(([f, m]) => (
                        <tr key={e.ticket_id + f}>
                          <td className="py-2 font-mono">{e.ticket_id}</td><td className="text-fg-muted">{FIELD[f] ?? f}</td>
                          <td>{valueLabel(f, m.expected)}</td>
                          <td>{Object.entries(m.got).map(([v, n]) => `${valueLabel(f, v)} (${n} of ${e.runs})`).join(", ")}</td>
                        </tr>
                      )))}
                  </tbody>
                </table>
              </div>
            )}
            {!isDev && <p className="mt-3 text-xs text-fg-faint">Priority mismatches are hidden in this view; Developer Mode shows every field. See docs/EVAL.md for why each mistake happens.</p>}
          </Card>

          {isDev && (
            <div className="mt-5 space-y-5">
              <DevPanel title="Run configuration and raw metrics" explain="From eval/runs/summary.json. Golden labels are read only by relay/evaluation.py, never by the decision path.">
                {ev.data.runs.length > 1 && (
                  <select className="input mb-3" value={s.eval_id} onChange={(e) => setPick(e.target.value)}>
                    {ev.data.runs.map((r) => <option key={r.eval_id} value={r.eval_id}>{r.eval_id}</option>)}
                  </select>
                )}
                <div className="grid gap-2 font-mono text-xs md:grid-cols-2">
                  <div>eval_id={s.eval_id}</div><div>config={JSON.stringify(s.config)}</div>
                  <div>priority_accuracy={fmt.pct(s.aggregate.priority_accuracy)}</div>
                  <div>tokens in/out={fmt.int(s.aggregate.tokens_in_total)}/{fmt.int(s.aggregate.tokens_out_total)}</div>
                  <div>wall time={s.wall_time_s.total.toFixed(1)}s ({s.wall_time_s.per_run.map((w) => `${w}s`).join(", ")})</div>
                  <div>unstable={s.aggregate.unstable_tickets.join(", ") || "none"} exceptions={s.exceptions.length}</div>
                  {s.per_run.map((r, i) => <div key={i}>run {i + 1}: action={fmt.pct(r.action_accuracy)} category={fmt.pct(r.category_accuracy)} tokens={r.tokens_in}/{r.tokens_out}</div>)}
                </div>
              </DevPanel>
              <DevPanel title="Raw run records (eval/runs/run_N.jsonl)" explain="One decisions.jsonl-schema row per golden ticket per round.">
                <div className="mb-3 flex flex-wrap gap-2">
                  {ev.data.files.map((f) => <button key={f} className={file === f ? "btn-primary" : "btn-ghost"} onClick={() => setFile(f)}>{f}</button>)}
                </div>
                {file && (!rows.data ? <Loading /> : (
                  <div className="max-h-96 overflow-auto">
                    <table className="w-full font-mono text-xs">
                      <thead className="sticky top-0 bg-[#0e1520] text-left text-fg-faint"><tr>{["ticket", "category", "priority", "action", "injection", "status", "tokens", "latency"].map((h) => <th key={h} className="pb-2">{h}</th>)}</tr></thead>
                      <tbody className="divide-y divide-ink-800">
                        {rows.data.map((r) => (
                          <tr key={String(r.ticket_id)}>
                            <td className="py-1">{String(r.ticket_id)}</td><td>{String(r.category)}</td><td>{String(r.priority)}</td><td>{String(r.action)}</td>
                            <td>{String(r.injection_detected)}</td><td>{String(r.status)}</td><td>{String(r.tokens_in)}/{String(r.tokens_out)}</td><td>{fmt.ms(Number(r.latency_ms))}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ))}
              </DevPanel>
            </div>
          )}
        </>
      )}
      <ConfirmDialog open={confirm} title="Run the evaluation?" confirmLabel="Start evaluation" onConfirm={start} onCancel={() => setConfirm(false)}>
        <p>This runs the 24 sample tickets through the AI 5 times (about 10 minutes on a laptop) and replaces the stored evaluation results.</p>
        <p>It does not change, approve or send any real ticket.</p>
      </ConfirmDialog>
    </>
  );
}
