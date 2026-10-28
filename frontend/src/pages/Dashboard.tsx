import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Card, DevPanel, Empty, ErrorBox, Help, Loading, PageHeader, Stat } from "../components/ui";
import { fmt, useApi, useEventStream, type RelayEvent } from "../lib/api";
import { useMode } from "../lib/mode";
import { ACTION, TERM } from "../lib/status";

interface Metrics {
  decisions: number; pending_approvals: number; escalated: number; failed: number; held_budget: number; outbox: number;
  tokens_in: number; tokens_out: number; model_calls: number; model_errors: number; usage_unreported: number;
  success_rate: number | null; avg_ticket_latency_ms: number | null;
  queue: { queued: number; leased: number; done: number; dead: number };
  by_action: Record<string, number>;
  by_model: { model: string; task: string; calls: number; tokens_in: number; tokens_out: number; avg_latency_ms: number }[];
  recent_events: RelayEvent[];
}
interface Summary { total: number; by_state: Record<string, number> }

export default function Dashboard() {
  const summary = useApi<Summary>("/api/tickets/summary", 8000);
  const m = useApi<Metrics>("/api/metrics", 8000);
  const { isDev } = useMode();

  if (summary.error) return <ErrorBox error={summary.error} onRetry={summary.reload} />;
  if (!summary.data) return <Loading label="Loading dashboard" />;
  const s = summary.data.by_state;
  const attention = (s.waiting_approval ?? 0) + (s.escalated ?? 0);

  return (
    <>
      <PageHeader
        title="Dashboard"
        purpose="Relay reads customer support tickets for two companies, drafts replies with a local AI model, and waits for a person to approve every reply before it reaches a customer."
        next={summary.data.total === 0 ? "Load some tickets on the Tickets page to get started." :
          attention ? "Start with the items that need your attention below." : "Nothing needs you right now. Open Tickets to process new ones."}
      />

      {summary.data.total === 0 ? (
        <Empty title="No data yet" action={<Link className="btn-primary" to="/tickets">Go to Tickets</Link>}>
          No tickets have been loaded. On the Tickets page you can import the assessment's inbox files (this does not process them), or run <code className="mono">make demo</code>.
        </Empty>
      ) : (
        <>
          <h2 className="mb-3 text-lg font-semibold">Needs your attention</h2>
          <div className="mb-8 grid gap-3 sm:grid-cols-3">
            <Stat label="Waiting for your approval" value={s.waiting_approval} tone={s.waiting_approval ? "attention" : undefined}
              help="Replies the AI prepared. Review each one and approve or reject it." to="/approvals" />
            <Stat label="Need human attention" value={s.escalated} tone={s.escalated ? "warning" : undefined}
              help="Tickets the system handed to a person (for example clinical questions or legal threats)." to="/escalations" />
            <Stat label="Not processed yet" value={s.not_processed}
              help="Received tickets the AI has not looked at. Open one and choose “Process this ticket”." to="/tickets?state=not_processed" />
          </div>

          <h2 className="mb-3 text-lg font-semibold">All tickets at a glance</h2>
          <div className="mb-8 grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Total tickets" value={summary.data.total} help="Each ticket counts once." to="/tickets" />
            <Stat label="Replies sent" value={s.sent} tone={s.sent ? "good" : undefined} help="Approved by a person and placed in the outbox." to="/tickets?state=sent" />
            <Stat label="Being processed" value={s.processing} help="The AI is working on these now." to="/tickets?state=processing" />
            <Stat label="Paused or failed" value={(s.held_budget ?? 0) + (s.failed ?? 0)} tone={s.failed ? "critical" : undefined}
              help="Stopped by the usage limit or by an error. They can be processed again." to={s.failed ? "/tickets?state=failed" : "/tickets?state=held_budget"} />
          </div>

          <Card title="How a ticket moves through Relay" className="mb-6">
            <ol className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-5">
              {[
                ["Received", "The ticket is stored. Nothing else happens yet."],
                ["Understood", "A small AI model works out the topic and urgency, and spots manipulation attempts."],
                ["Checked", "Safety rules decide: reply, ask for details, or hand to a person (clinical, legal, disputes)."],
                ["Drafted", "A larger AI model writes a reply using only the company's knowledge base; rule checks verify it."],
                ["Approved", "A person approves or rejects. Only approved replies reach the customer, exactly once."],
              ].map(([t, d], i) => (
                <li key={t} className="rounded-md border border-ink-700 bg-ink-850 p-3">
                  <div className="font-medium"><span className="mr-1.5 text-accent">{i + 1}.</span>{t}</div>
                  <div className="mt-1 text-fg-muted">{d}</div>
                </li>
              ))}
            </ol>
          </Card>
        </>
      )}

      {isDev && <DevSection m={m.data} error={m.error} />}
    </>
  );
}

function DevSection({ m, error }: { m: Metrics | undefined; error: unknown }) {
  const [live, setLive] = useState<RelayEvent[]>([]);
  const [after, setAfter] = useState<number | null>(null);
  useEffect(() => {
    if (m && after === null) setAfter(m.recent_events[0]?.event_id ?? 0);
  }, [m, after]);
  const stream = useEventStream(after === null ? null : `/api/stream?after=${after}`, (e) =>
    setLive((prev) => (prev.some((p) => p.event_id === e.event_id) ? prev : [e, ...prev].slice(0, 40))));
  if (error) return <ErrorBox error={error} />;
  if (!m) return <Loading />;
  const seen = new Set(live.map((e) => e.event_id));
  const events = [...live, ...m.recent_events.filter((e) => !seen.has(e.event_id))].slice(0, 25);

  return (
    <div className="space-y-5">
      <DevPanel title="Model gateway meter" explain="Every model call is metered by the gateway from Ollama's own counts. Evaluation traffic is excluded.">
        <div className="mb-4 grid grid-cols-2 gap-3 font-mono text-sm lg:grid-cols-5">
          {[
            ["decisions", m.decisions], ["model_calls", m.model_calls], ["tokens_in", m.tokens_in], ["tokens_out", m.tokens_out],
            ["avg ticket latency", fmt.ms(m.avg_ticket_latency_ms)], ["success_rate", fmt.pct(m.success_rate)],
            ["model_errors", m.model_errors], ["usage unreported", m.usage_unreported],
            ["queue queued/leased", `${m.queue.queued}/${m.queue.leased}`], ["queue done/dead", `${m.queue.done}/${m.queue.dead}`],
          ].map(([k, v]) => (
            <div key={String(k)} className="rounded border border-series-1/20 px-3 py-2">
              <div className="text-[11px] text-fg-faint">{k}</div><div className="text-base text-fg">{typeof v === "number" ? fmt.int(v) : v}</div>
            </div>
          ))}
        </div>
        <table className="w-full font-mono text-xs">
          <thead className="text-left text-fg-faint">
            <tr><th className="pb-2">model</th><th className="pb-2">task</th><th className="pb-2">calls</th>
              <th className="pb-2"><span className="inline-flex gap-1">tokens_in <Help text={TERM.tokens_in} /></span></th>
              <th className="pb-2">tokens_out</th><th className="pb-2">avg latency</th></tr>
          </thead>
          <tbody className="divide-y divide-ink-800 tabular-nums">
            {m.by_model.map((r) => (
              <tr key={r.model + r.task}><td className="py-1.5">{r.model}</td><td>{r.task}</td><td>{r.calls}</td><td>{fmt.int(r.tokens_in)}</td><td>{fmt.int(r.tokens_out)}</td><td>{fmt.ms(r.avg_latency_ms)}</td></tr>
            ))}
          </tbody>
        </table>
        <p className="mt-3 font-mono text-xs text-fg-faint">decisions by action: {Object.entries(m.by_action).map(([k, v]) => `${k}=${v} (${ACTION[k]?.label ?? k})`).join(" · ") || "none"}</p>
      </DevPanel>
      <DevPanel title={`Execution event stream (SSE: ${stream})`} explain="Raw workflow events from all tickets you may see, newest first, as recorded in Postgres. Live: new events arrive without refreshing.">
        {!events.length ? <p className="text-sm text-fg-faint">No events recorded yet.</p> : (
          <ol className="divide-y divide-ink-800 font-mono text-xs">
            {events.map((e) => (
              <li key={e.event_id} className="grid grid-cols-[3.5rem_4.5rem_1fr_auto] gap-3 py-1.5">
                <span className="text-fg-faint">#{e.event_id}</span><span className="text-fg-faint">{fmt.time(e.at)}</span>
                <Link className="truncate hover:text-[#8fbcf2]" to={`/tickets/${e.tenant_id}/${encodeURIComponent(e.ticket_id ?? "")}`}>
                  {e.tenant_id}/{e.ticket_id} · {e.node} · {e.event_type}
                </Link>
                <span>{e.duration_ms != null ? fmt.ms(e.duration_ms) : ""} {e.status}</span>
              </li>
            ))}
          </ol>
        )}
      </DevPanel>
    </div>
  );
}
