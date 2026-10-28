import { Card, ErrorBox, Loading, PageHeader } from "../components/ui";
import { fmt, useApi } from "../lib/api";
import { useMode } from "../lib/mode";

interface Health { ok: boolean; checks: Record<string, { ok: boolean; detail: string; checked_ms: number }> }

const LABELS: Record<string, { name: string; what: string }> = {
  postgres: { name: "Database", what: "Stores tickets, decisions, approvals and the audit trail." },
  pgvector: { name: "Knowledge search", what: "Lets the system find the right knowledge-base article." },
  ollama: { name: "Local AI service", what: "Runs the AI models on this computer (Ollama)." },
  required_models: { name: "AI models installed", what: "The three models the system needs are downloaded." },
  workers: { name: "Background workers", what: "Processes tickets from the queue. Idle is normal between runs." },
  queue: { name: "Work queue", what: "Tickets waiting to be processed or retried." },
  checkpoints: { name: "Saved progress", what: "Paused and in-progress work is saved and isolated per company." },
  sse: { name: "Live updates", what: "Pushes progress to this console without refreshing." },
};

export default function Health() {
  const h = useApi<Health>("/health", 15000);
  const { isDev } = useMode();
  return (
    <>
      <PageHeader
        title="System health"
        purpose="Whether each part the system depends on is working right now. Every check runs against the live service when this page loads and every 15 seconds; a check that could not run is shown as a problem, never as healthy."
        next="If something shows a problem, follow docs/RUNBOOK.md (for example: start Ollama, or run make db)."
        actions={<button className="btn-ghost" onClick={h.reload}>Check again</button>}
      />
      {h.error && <ErrorBox error={h.error} onRetry={h.reload} />}
      {!h.data && !h.error && <Loading label="Running health checks" />}
      {h.data && (
        <Card title={h.data.ok ? "Everything is working" : "Something needs attention"}>
          <ul className="divide-y divide-ink-800">
            {Object.entries(h.data.checks).map(([k, c]) => (
              <li key={k} className="grid grid-cols-[1.5rem_1fr] gap-3 py-3 text-sm sm:grid-cols-[1.5rem_14rem_1fr]">
                <span className={c.ok ? "text-[#3ecf7a]" : "text-[#ef6b6b]"} aria-label={c.ok ? "working" : "problem"}>{c.ok ? "✓" : "✕"}</span>
                <span><span className="font-medium">{LABELS[k]?.name ?? k}</span><span className="block text-xs text-fg-faint">{LABELS[k]?.what}</span></span>
                <span className="col-span-2 text-fg-muted sm:col-span-1">
                  {c.ok ? "Working" : "Problem"}: {c.detail}
                  {isDev && <span className="ml-2 font-mono text-xs text-[#8fbcf2]">{k} · {fmt.ms(c.checked_ms)}</span>}
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </>
  );
}
