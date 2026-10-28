import { Link, useSearchParams } from "react-router-dom";
import { ModelCalls, NodeTimeline, ProgressTracker } from "../components/Execution";
import { Card, DevPanel, Empty, ErrorBox, Help, KV, Loading, PageHeader } from "../components/ui";
import { fmt, useApi, type RelayEvent } from "../lib/api";
import { useMode } from "../lib/mode";
import { ACTION, TENANT, TERM } from "../lib/status";
import { useThreadEvents, type WorkflowRow } from "../lib/thread";

interface RunDetail {
  workflow: WorkflowRow;
  events: RelayEvent[];
  model_calls: { task: string; model: string; tokens_in: number | null; tokens_out: number | null; latency_ms: number; ok: boolean; error_class: string | null; at: string }[];
  job: { job_id: number; status: string; attempts: number; max_attempts: number; lease_owner: string | null; lease_expires_at: string | null; last_error: string | null } | null;
}

const RUN_STATE: Record<string, string> = {
  running: "processing", awaiting_approval: "waiting_approval", completed: "sent", failed: "failed", held_budget: "held_budget",
};

export default function Runs() {
  const [params, setParams] = useSearchParams();
  const thread = params.get("thread");
  const runs = useApi<WorkflowRow[]>("/api/runs?limit=200", 10000);
  const { isDev } = useMode();

  return (
    <>
      <PageHeader
        title="Run history"
        purpose="Every time the AI processed a ticket, newest first. A ticket processed again (for example after a failure) has more than one run."
        next="Select a run to see how far it got. Developer Mode adds the step-by-step technical record."
      />
      {runs.error && <ErrorBox error={runs.error} onRetry={runs.reload} />}
      <div className="grid gap-5 lg:grid-cols-[minmax(0,380px)_1fr]">
        <Card>
          {!runs.data ? <Loading /> : !runs.data.length ? <Empty title="No runs yet">Process a ticket from its page to create a run.</Empty> : (
            <ul className="-m-2 max-h-[70vh] space-y-1 overflow-auto">
              {runs.data.map((r) => (
                <li key={r.thread_id}>
                  <button onClick={() => setParams({ thread: r.thread_id })} aria-current={thread === r.thread_id}
                    className={`w-full rounded-md px-3 py-2 text-left ${thread === r.thread_id ? "bg-ink-800 ring-1 ring-accent/40" : "hover:bg-ink-850"}`}>
                    <div className="flex items-center justify-between gap-2 text-sm">
                      <span><span className="font-mono">{r.ticket_id}</span> <span className="text-fg-faint">· {TENANT[r.tenant_id] ?? r.tenant_id}</span></span>
                      <span className="text-xs text-fg-faint">{fmt.datetime(r.started_at)}</span>
                    </div>
                    <div className="mt-1 text-xs text-fg-muted">
                      {r.decision_status === "escalated" ? "Handed to a person" : RUN_STATE[r.status] === "waiting_approval" ? "Waiting for approval" :
                        r.status === "completed" ? "Finished" : fmt.label(r.status)}
                      {isDev && <span className="ml-2 font-mono text-[#8fbcf2]">{r.thread_id}</span>}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
        {thread ? <RunPanel key={thread} thread={thread} /> : <Card><Empty title="Select a run to view its details">Nothing is selected automatically.</Empty></Card>}
      </div>
      {!isDev && thread && <p className="mt-3 text-xs text-fg-faint">Tip: switch to Developer Mode at the top to see node-level timings, model calls and token counts for this run.</p>}
    </>
  );
}

function RunPanel({ thread }: { thread: string }) {
  const d = useApi<RunDetail>(`/api/runs/${encodeURIComponent(thread)}`, 8000);
  const { events: live } = useThreadEvents(thread);
  const { isDev } = useMode();
  if (d.error) return <ErrorBox error={d.error} onRetry={d.reload} />;
  if (!d.data) return <Card><Loading /></Card>;
  const byId = new Map<number, RelayEvent>();
  for (const e of [...d.data.events, ...live]) byId.set(e.event_id, e);
  const events = [...byId.values()].sort((a, b) => a.event_id - b.event_id);
  const w = d.data.workflow;
  const job = d.data.job;

  return (
    <div className="space-y-5">
      <Card title={<>Run for <span className="font-mono">{w.ticket_id}</span></>}>
        <KV rows={[
          ["Company", TENANT[w.tenant_id] ?? w.tenant_id],
          ["Started", fmt.datetime(w.started_at)],
          ["Outcome", w.action ? ACTION[w.action]?.label ?? w.action : "Not decided yet"],
          ["Time to decision", fmt.ms(w.latency_ms)],
        ]} />
        <div className="mt-4"><ProgressTracker events={events} /></div>
        <Link className="btn-ghost mt-4" to={`/tickets/${w.tenant_id}/${encodeURIComponent(w.ticket_id)}`}>Open the ticket</Link>
      </Card>
      {isDev && (
        <>
          <div className="grid gap-5 md:grid-cols-2">
            <DevPanel title="Correlation" explain={TERM.correlation}>
              <KV rows={[["thread_id", <code key="t" className="mono">{w.thread_id}</code>], ["run_id", <code key="r" className="mono">{w.run_id}</code>],
                ["status", <code key="s" className="mono">{w.status}</code>], ["current_node", <code key="c" className="mono">{w.current_node ?? "-"}</code>],
                [<span key="k" className="inline-flex items-center gap-1">checkpointer <Help text={TERM.checkpoint} /></span>, "PostgresSaver, tenant-bound, durability=sync"]]} />
            </DevPanel>
            <DevPanel title="Queue lease" explain={TERM.lease}>
              {job ? <KV rows={[["job", `#${job.job_id} · ${job.status}`], ["attempts", `${job.attempts} / ${job.max_attempts}`],
                ["lease owner", job.lease_owner ?? "none (released)"], ["lease expires", fmt.datetime(job.lease_expires_at)], ["last error", job.last_error ?? "none"]]} />
                : <p className="text-sm text-fg-faint">No queue job recorded for this run.</p>}
            </DevPanel>
          </div>
          <DevPanel title="Model calls" explain="Metered by the gateway with Ollama's reported token counts."><ModelCalls calls={d.data.model_calls} /></DevPanel>
          <DevPanel title="LangGraph node timeline" explain="Every recorded event for this thread, in order."><NodeTimeline events={events} /></DevPanel>
        </>
      )}
    </div>
  );
}
