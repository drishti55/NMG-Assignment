import { useSearchParams } from "react-router-dom";
import { Card, CheckRules, Empty, ErrorBox, Json, KV, Loading, PageHeader, Pill, StatusBadge } from "../components/ui";
import { fmt, useApi, type RelayEvent } from "../lib/api";
import { useDevMode } from "../lib/devmode";
import { useThreadEvents, type WorkflowRow } from "../lib/thread";

interface RunDetail {
  workflow: WorkflowRow;
  events: RelayEvent[];
  model_calls: { call_id: number; task: string; model: string; tokens_in: number | null; tokens_out: number | null; latency_ms: number; ok: boolean; error_class: string | null; at: string }[];
  job: { job_id: number; status: string; attempts: number; max_attempts: number; lease_owner: string | null; lease_expires_at: string | null; last_error: string | null; available_at: string } | null;
}

interface TicketDetail {
  ticket: { subject: string; body: string; customer_ref: string };
  decision: { action: string; status: string; category: string; priority: string; injection_detected: boolean; draft_reply: string | null; kb_refs: string[]; reason_code: string | null } | null;
  approvals: { approval_id: string; attempt: number; status: string; decided_by: string | null; reason: string | null; check_result: { passed: boolean; rules: { rule_id: string; passed: boolean; message: string }[] } }[];
  outbox: { approved_by: string; approved_at: string } | null;
}

const SAFE_ERRORS: Record<string, string> = {
  model_unavailable: "The language model service was unreachable; the job is retried with backoff.",
  model_timeout: "The model did not answer in time; the job is retried with backoff.",
  model_missing: "A required model is not installed; an operator must pull it.",
  budget_exceeded: "The token budget ran out, so the ticket is held instead of processed.",
  invalid_model_output: "The model returned output that failed validation twice.",
};

const LEAD_NODES = new Set(["validate_input", "validate_tenant", "route_decision", "request_information", "repair_invalid_output",
  "escalate_ticket", "record_decision", "await_human_approval", "prepare_redraft", "write_approved_outbox", "record_failure",
  "finalize_run", "checker_failed", "rejected_twice", "orchestrator", "queue"]);

export default function Inspector() {
  const [params, setParams] = useSearchParams();
  const runs = useApi<WorkflowRow[]>("/api/runs?limit=200");
  const thread = params.get("thread") || runs.data?.[0]?.thread_id || null;
  const detail = useApi<RunDetail>(thread ? `/api/runs/${encodeURIComponent(thread)}` : null, 5000);
  const [tenant, ticketId] = (thread ?? "").split(":");
  const ticket = useApi<TicketDetail>(thread ? `/api/tickets/${tenant}/${encodeURIComponent(ticketId)}` : null, 5000);
  const { events } = useThreadEvents(thread);
  const { on: dev, toggle } = useDevMode();

  return (
    <>
      <PageHeader
        title="Run inspector"
        subtitle={dev
          ? "Developer mode: observable execution telemetry for one LangGraph thread. Read-only; no hidden reasoning is recorded or shown."
          : "Operations view of one ticket's run. Turn on Developer mode for node-level telemetry."}
        actions={<button className={dev ? "btn-primary" : "btn-ghost"} onClick={toggle}>Developer mode: {dev ? "ON" : "OFF"}</button>}
      />
      <div className="mb-4">
        <select className="input w-full max-w-xl" value={thread ?? ""} onChange={(e) => setParams({ thread: e.target.value })}>
          {runs.data?.map((r) => (
            <option key={r.thread_id} value={r.thread_id}>{r.thread_id} · {fmt.label(r.status)}</option>
          ))}
        </select>
      </div>
      {!thread && runs.data && <Empty title="No runs yet" />}
      {detail.error && <ErrorBox error={detail.error} onRetry={detail.reload} />}
      {thread && !detail.data && !detail.error && <Loading rows={6} />}
      {detail.data && ticket.data && (dev ? <DevView d={detail.data} events={events} /> : <OpsView d={detail.data} t={ticket.data} />)}
    </>
  );
}

function OpsView({ d, t }: { d: RunDetail; t: TicketDetail }) {
  const dec = t.decision;
  const latest = t.approvals.at(-1);
  const failure = d.events.find((e) => e.event_type === "node_failed" || e.event_type === "job_dead_lettered");
  const errClass = failure ? String(failure.metadata.error_class ?? "") : null;
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Ticket">
        <KV rows={[
          ["Ticket", `${d.workflow.ticket_id} (${d.workflow.tenant_id})`],
          ["Customer", t.ticket.customer_ref],
          ["Subject", t.ticket.subject],
          ["Run status", <StatusBadge key="s" status={d.workflow.status} />],
        ]} />
        <p className="mt-3 whitespace-pre-wrap rounded-md bg-ink-850 p-3 text-sm text-fg-muted">{t.ticket.body}</p>
      </Card>
      <Card title="Decision">
        {!dec ? <p className="text-sm text-fg-faint">No decision yet.</p> : (
          <KV rows={[
            ["Action", fmt.label(dec.action)],
            ["Status", <StatusBadge key="s" status={dec.status} />],
            ["Category / priority", `${fmt.label(dec.category)} · ${dec.priority}`],
            ["Policy outcome", dec.injection_detected ? "Instructions aimed at the system detected and not followed" : fmt.label(dec.reason_code)],
            ["Approval", latest ? <StatusBadge key="a" status={latest.status} /> : "not required"],
            ["Sent to customer", t.outbox ? `yes, approved by ${t.outbox.approved_by}` : "no"],
          ]} />
        )}
        {errClass && <div className="mt-3"><ErrorBox error={SAFE_ERRORS[errClass] ?? "The run hit an error and was handled by the retry policy."} /></div>}
      </Card>
      <Card title="Proposed response" className="lg:col-span-2">
        {dec?.draft_reply ? (
          <>
            <p className="whitespace-pre-wrap text-sm leading-relaxed">{dec.draft_reply}</p>
            <div className="mt-2 flex gap-1.5">{dec.kb_refs.map((k) => <Pill key={k}>{k}</Pill>)}</div>
          </>
        ) : <p className="text-sm text-fg-faint">Nothing drafted (escalated, held or failed tickets never get a customer draft).</p>}
        {latest && <div className="mt-4"><div className="label mb-1">Checker</div><CheckRules rules={latest.check_result.rules} /></div>}
      </Card>
    </div>
  );
}

function DevView({ d, events }: { d: RunDetail; events: RelayEvent[] }) {
  const evs = events.length ? events : d.events;
  const totals = d.model_calls.reduce((a, c) => ({ in: a.in + (c.tokens_in ?? 0), out: a.out + (c.tokens_out ?? 0), ms: a.ms + c.latency_ms }), { in: 0, out: 0, ms: 0 });
  return (
    <div className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Correlation">
          <KV rows={[
            ["thread_id", <code key="t" className="mono">{d.workflow.thread_id}</code>],
            ["run_id", <code key="r" className="mono">{d.workflow.run_id}</code>],
            ["status", <StatusBadge key="s" status={d.workflow.status} />],
            ["current node", <code key="c" className="mono">{d.workflow.current_node ?? "-"}</code>],
            ["checkpointer", "PostgresSaver (durability=sync)"],
          ]} />
        </Card>
        <Card title="Queue lease">
          {d.job ? (
            <KV rows={[
              ["job", `#${d.job.job_id} · ${d.job.status}`],
              ["attempts", `${d.job.attempts} / ${d.job.max_attempts}`],
              ["lease owner", d.job.lease_owner ?? "none (released)"],
              ["lease expires", fmt.datetime(d.job.lease_expires_at)],
              ["last error", d.job.last_error ?? "none"],
            ]} />
          ) : <p className="text-sm text-fg-faint">No queue job (submitted outside the queue).</p>}
        </Card>
        <Card title="Model usage (this thread)">
          <KV rows={[
            ["calls", String(d.model_calls.length)],
            ["tokens in / out", `${fmt.int(totals.in)} / ${fmt.int(totals.out)}`],
            ["model time", fmt.ms(totals.ms)],
            ["source", "Ollama prompt_eval_count / eval_count"],
          ]} />
        </Card>
      </div>

      <Card title="Model calls">
        <table className="w-full text-sm">
          <thead className="text-left text-fg-faint">
            <tr>{["Time", "Task", "Model", "Tokens in", "Tokens out", "Latency", "Result"].map((h) => <th key={h} className="pb-2 font-medium">{h}</th>)}</tr>
          </thead>
          <tbody className="divide-y divide-ink-800 tabular-nums">
            {d.model_calls.map((c) => (
              <tr key={c.call_id}>
                <td className="mono py-1.5 text-fg-faint">{fmt.time(c.at)}</td>
                <td>{c.task}</td>
                <td className="mono">{c.model}</td>
                <td>{c.tokens_in ?? <span className="text-fg-faint">unreported</span>}</td>
                <td>{c.tokens_out ?? <span className="text-fg-faint">unreported</span>}</td>
                <td>{fmt.ms(c.latency_ms)}</td>
                <td>{c.ok ? <StatusBadge status="done" /> : <span className="text-[#ef6b6b]">✕ {c.error_class}</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <Card title="Node timeline" right={<span className="text-xs text-fg-faint">lead = deterministic orchestration · specialist = agent</span>}>
        <ol className="space-y-2">
          {evs.filter((e) => e.event_type !== "node_started").map((e) => {
            const lead = LEAD_NODES.has(e.node ?? "");
            const m = e.metadata as Record<string, unknown>;
            return (
              <li key={e.event_id} className={`rounded-md border px-3 py-2 ${lead ? "border-ink-700" : "ml-6 border-accent/30 bg-accent-soft/20"}`}>
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
                  <span className="mono text-fg-faint">#{e.event_id} {fmt.time(e.at)}</span>
                  <span className="mono font-medium">{e.node}</span>
                  <Pill tone={lead ? "neutral" : "accent"}>{lead ? "lead" : "specialist"}</Pill>
                  <span className="text-fg-faint">{fmt.label(e.event_type)}</span>
                  {typeof m.contract === "string" && <Pill>{m.contract}</Pill>}
                  {typeof m.model === "string" && <span className="mono text-fg-muted">{m.model} · {String(m.tokens_in ?? "?")}→{String(m.tokens_out ?? "?")} tok · {fmt.ms(Number(m.model_latency_ms))}</span>}
                  <span className="ml-auto flex items-center gap-2">
                    {e.duration_ms != null && <span className="mono text-fg-faint">{fmt.ms(e.duration_ms)}</span>}
                    <StatusBadge status={e.status} />
                  </span>
                </div>
                <NodeDetail m={m} />
              </li>
            );
          })}
        </ol>
      </Card>
    </div>
  );
}

function NodeDetail({ m }: { m: Record<string, unknown> }) {
  const parts: JSX.Element[] = [];
  if (typeof m.reason_code === "string")
    parts.push(<div key="r" className="text-sm text-fg-muted">routing: <span className="text-fg">{String(m.action)}</span> because <span className="mono">{m.reason_code}</span>
      {Array.isArray(m.overrides) && m.overrides.length > 0 && <> · overrides: <span className="mono">{(m.overrides as string[]).join(", ")}</span></>}</div>);
  if (typeof m.injection_score === "number")
    parts.push(<div key="i" className="text-sm text-fg-muted">injection detector score {m.injection_score} {Array.isArray(m.injection_signals) && m.injection_signals.length ? `(${(m.injection_signals as string[]).join(", ")})` : ""}</div>);
  if (Array.isArray(m.passages))
    parts.push(<div key="p" className="flex flex-wrap gap-1.5 text-sm text-fg-muted">retrieved: {(m.passages as { kb_id: string; score: number }[]).map((p) => <Pill key={p.kb_id}>{p.kb_id} · {p.score.toFixed(3)}</Pill>)}{m.adequate === false && <span>no adequate evidence</span>}</div>);
  if (Array.isArray(m.rules)) parts.push(<div key="c" className="mt-1"><CheckRules rules={m.rules as { rule_id: string; passed: boolean; message: string }[]} /></div>);
  if (typeof m.error_class === "string") parts.push(<div key="e" className="text-sm text-[#ef6b6b]">error class: {m.error_class}{m.transient ? " (transient → retry)" : ""}</div>);
  const rest = Object.fromEntries(Object.entries(m).filter(([k]) => !["rules", "passages", "contract", "model", "tokens_in", "tokens_out", "model_latency_ms", "reason_code", "overrides", "action", "injection_score", "injection_signals"].includes(k)));
  if (!parts.length && Object.keys(rest).length) parts.push(<div key="j" className="mt-1"><Json value={rest} /></div>);
  return parts.length ? <div className="mt-1.5 space-y-1">{parts}</div> : null;
}
