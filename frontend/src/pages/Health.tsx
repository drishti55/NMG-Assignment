import { Card, ErrorBox, Loading, PageHeader } from "../components/ui";
import { fmt, useApi } from "../lib/api";

interface Health { ok: boolean; checks: Record<string, { ok: boolean; detail: string; checked_ms: number }> }

const LABELS: Record<string, string> = {
  postgres: "PostgreSQL", pgvector: "pgvector", ollama: "Ollama", required_models: "Required models",
  workers: "Workflow workers", queue: "Queue", checkpoints: "LangGraph checkpoints", sse: "SSE delivery",
};

export default function Health() {
  const h = useApi<Health>("/health", 15000);
  return (
    <>
      <PageHeader
        title="System health"
        subtitle="Each check runs against the live dependency when this page loads (and every 15s). A check that could not run is reported as failed or not checked, never as healthy."
        actions={<button className="btn-ghost" onClick={h.reload} disabled={h.loading}>{h.loading ? "Checking…" : "Re-run checks"}</button>}
      />
      {h.error && <ErrorBox error={`Health endpoint unreachable: ${h.error}. Is the API running (make api)?`} onRetry={h.reload} />}
      {!h.data && !h.error && <Loading rows={8} />}
      {h.data && (
        <Card>
          <ul className="divide-y divide-ink-800">
            {Object.entries(h.data.checks).map(([k, c]) => (
              <li key={k} className="grid grid-cols-[1.5rem_11rem_1fr_auto] items-center gap-3 py-2.5 text-sm">
                <span className={c.ok ? "text-status-good" : "text-[#ef6b6b]"} aria-label={c.ok ? "ok" : "failing"}>{c.ok ? "✓" : "✕"}</span>
                <span className="font-medium">{LABELS[k] ?? k}</span>
                <span className="text-fg-muted">{c.detail}</span>
                <span className="mono text-fg-faint">{fmt.ms(c.checked_ms)}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </>
  );
}
