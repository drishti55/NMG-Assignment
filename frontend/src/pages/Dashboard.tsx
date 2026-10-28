import { useEffect, useState } from "react";
import { EventFeed } from "../components/EventFeed";
import { BarList, Card, ErrorBox, Loading, PageHeader, Stat } from "../components/ui";
import { fmt, useApi, useEventStream, type RelayEvent } from "../lib/api";

interface Metrics {
  decisions: number;
  pending_approvals: number;
  escalated: number;
  failed: number;
  held_budget: number;
  outbox: number;
  tokens_in: number;
  tokens_out: number;
  model_calls: number;
  model_errors: number;
  usage_unreported: number;
  success_rate: number | null;
  avg_ticket_latency_ms: number | null;
  queue: { queued: number; leased: number; done: number; dead: number };
  by_action: Record<string, number>;
  by_category: Record<string, number>;
  by_model: { model: string; task: string; calls: number; tokens_in: number; tokens_out: number; avg_latency_ms: number }[];
  recent_events: RelayEvent[];
}

export default function Dashboard() {
  const m = useApi<Metrics>("/api/metrics", 5000);
  const [live, setLive] = useState<RelayEvent[]>([]);
  // The stream starts after the newest event in the first snapshot; later polls don't move it.
  const [after, setAfter] = useState<number | null>(null);
  useEffect(() => {
    if (m.data && after === null) setAfter(m.data.recent_events[0]?.event_id ?? 0);
  }, [m.data, after]);
  const stream = useEventStream(after === null ? null : `/api/stream?after=${after}`, (e) =>
    setLive((prev) => [e, ...prev].slice(0, 50)),
  );
  if (m.error) return <ErrorBox error={m.error} onRetry={m.reload} />;
  if (!m.data) return <Loading rows={6} />;
  const d = m.data;
  const seen = new Set(live.map((e) => e.event_id));
  const events = [...live, ...d.recent_events.filter((e) => !seen.has(e.event_id))];

  return (
    <>
      <PageHeader
        title="Operations"
        subtitle="Every figure is read from Postgres: decisions, approvals, model calls metered by the gateway, and the work queue. Evaluation traffic is excluded."
      />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Stat label="Tickets decided" value={fmt.int(d.decisions)} hint={`${fmt.int(d.outbox)} replies sent`} />
        <Stat label="Pending approval" value={fmt.int(d.pending_approvals)} tone={d.pending_approvals ? "warning" : undefined} />
        <Stat label="Escalated" value={fmt.int(d.escalated)} />
        <Stat label="Failed" value={fmt.int(d.failed)} tone={d.failed ? "critical" : undefined} hint={`${fmt.int(d.held_budget)} held on budget`} />
        <Stat label="Success rate" value={fmt.pct(d.success_rate)} hint="decisions not failed" />
        <Stat label="Tokens in" value={fmt.int(d.tokens_in)} hint={`${fmt.int(d.model_calls)} model calls`} />
        <Stat label="Tokens out" value={fmt.int(d.tokens_out)} hint={d.usage_unreported ? `${d.usage_unreported} calls without reported usage` : "as reported by Ollama"} />
        <Stat label="Avg ticket latency" value={fmt.ms(d.avg_ticket_latency_ms)} hint="ticket start → decision" />
        <Stat label="Queue depth" value={fmt.int(d.queue.queued + d.queue.leased)} hint={`${d.queue.leased} leased · ${d.queue.dead} dead`} tone={d.queue.dead ? "critical" : undefined} />
        <Stat label="Model errors" value={fmt.int(d.model_errors)} tone={d.model_errors ? "warning" : undefined} hint="timeouts, invalid output" />
      </div>

      <div className="mt-6 grid gap-4 lg:grid-cols-[1fr_1fr]">
        <Card title="Outcomes by action">
          <BarList items={Object.entries(d.by_action).map(([label, value]) => ({ label, value }))} />
        </Card>
        <Card title="Tickets by category">
          <BarList items={Object.entries(d.by_category).sort((a, b) => b[1] - a[1]).map(([label, value]) => ({ label, value }))} />
        </Card>
      </div>

      <Card title="Model usage" className="mt-4">
        {d.by_model.length ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-fg-faint">
                <tr>{["Model", "Task", "Calls", "Tokens in", "Tokens out", "Avg latency"].map((h) => <th key={h} className="pb-2 font-medium">{h}</th>)}</tr>
              </thead>
              <tbody className="divide-y divide-ink-800 tabular-nums">
                {d.by_model.map((r) => (
                  <tr key={r.model + r.task}>
                    <td className="mono py-1.5">{r.model}</td>
                    <td className="text-fg-muted">{r.task}</td>
                    <td>{fmt.int(r.calls)}</td>
                    <td>{fmt.int(r.tokens_in)}</td>
                    <td>{fmt.int(r.tokens_out)}</td>
                    <td>{fmt.ms(r.avg_latency_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-fg-faint">No model calls recorded yet.</p>
        )}
      </Card>

      <Card
        title="Recent execution events"
        className="mt-4"
        right={<span className={`text-xs ${stream === "live" ? "text-accent" : "text-status-warning"}`}>● {stream}</span>}
      >
        <EventFeed events={events} />
      </Card>
    </>
  );
}
