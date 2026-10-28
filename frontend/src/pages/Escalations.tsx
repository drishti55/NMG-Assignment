import { Link, useSearchParams } from "react-router-dom";
import { Card, DevPanel, Empty, ErrorBox, Json, Loading, PageHeader } from "../components/ui";
import { fmt, useApi } from "../lib/api";
import { useMode } from "../lib/mode";
import { CATEGORY, TENANT, reasonText } from "../lib/status";

interface CaseRow {
  case_id: string; tenant_id: string; ticket_id: string; thread_id: string | null; kind: string; owner: string;
  severity: string; reason_code: string; reason: string; status: string; created_at: string;
  category: string | null; priority: string | null; injection_detected: boolean | null; subject: string | null;
  next_step: string;
  audit: { actor: string; action: string; from_state: string | null; to_state: string | null; reason: string | null; at: string }[];
}

const SEVERITY: Record<string, { label: string; cls: string }> = {
  sev1: { label: "Most urgent", cls: "text-[#ef6b6b] border-status-critical/50" },
  sev2: { label: "High", cls: "text-status-serious border-status-serious/50" },
  sev3: { label: "Normal", cls: "text-status-warning border-status-warning/50" },
  sev4: { label: "Low", cls: "text-fg-muted border-ink-600" },
};

export default function Escalations() {
  const cases = useApi<CaseRow[]>("/api/escalations", 15000);
  const [params, setParams] = useSearchParams();
  const open = params.get("case");
  const { isDev } = useMode();

  return (
    <>
      <PageHeader
        title="Escalations"
        purpose="Tickets the system handed to a person instead of answering: clinical questions, legal threats, money disputes, manipulation attempts, or requests the knowledge base does not cover. No reply was drafted for these."
        next="Open a case to see why it was escalated, who owns it and what to do next. Replies to these customers are written outside the AI."
      />
      {cases.error && <ErrorBox error={cases.error} onRetry={cases.reload} />}
      {!cases.data ? <Loading /> : !cases.data.length ? (
        <Empty title="No escalations">When a ticket needs a person, a case appears here with the reason and the owner.</Empty>
      ) : (
        <ul className="space-y-2">
          {cases.data.map((c) => {
            const expanded = open === c.case_id;
            const sev = SEVERITY[c.severity] ?? SEVERITY.sev3;
            return (
              <li key={c.case_id}>
                <Card>
                  <button className="w-full text-left" aria-expanded={expanded}
                    onClick={() => setParams(expanded ? {} : { case: c.case_id }, { replace: true })}>
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
                      <span className={`rounded border px-2 py-0.5 text-xs font-semibold ${sev.cls}`}>{sev.label}</span>
                      <span className="font-medium">{c.subject ?? c.ticket_id}</span>
                      <span className="font-mono text-sm text-fg-faint">{c.ticket_id}</span>
                      <span className="text-sm text-fg-faint">{TENANT[c.tenant_id] ?? c.tenant_id}</span>
                      {c.kind === "dead_letter" && <span className="text-xs text-[#ef6b6b]">Processing failed</span>}
                      <span className="ml-auto text-sm text-fg-muted">Owner: {c.owner.replace(/-/g, " ")}</span>
                    </div>
                    <p className="mt-2 text-sm text-fg-muted">{reasonText(c.reason_code)}</p>
                    <span className="mt-1 inline-block text-xs text-accent">{expanded ? "Hide details" : "Show details and next step"}</span>
                  </button>
                  {expanded && (
                    <div className="mt-4 grid gap-5 border-t border-ink-700 pt-4 md:grid-cols-2">
                      <div className="space-y-3 text-sm">
                        <div><div className="label mb-1">What to do next</div><p>{c.next_step}</p></div>
                        <div><div className="label mb-1">Case summary</div><p className="text-fg-muted">{c.reason}</p></div>
                        <div className="text-fg-muted">Topic: {CATEGORY[c.category ?? ""] ?? "—"} · Opened {fmt.datetime(c.created_at)}</div>
                        <Link className="btn-ghost" to={`/tickets/${c.tenant_id}/${encodeURIComponent(c.ticket_id)}`}>Open the ticket</Link>
                      </div>
                      <div>
                        <div className="label mb-1">History</div>
                        <ol className="space-y-1 text-sm text-fg-muted">
                          {c.audit.map((a, i) => (
                            <li key={i}><span className="text-fg-faint">{fmt.datetime(a.at)}</span> {fmt.label(a.action)}{a.reason ? ` (${a.reason})` : ""}</li>
                          ))}
                        </ol>
                      </div>
                      {isDev && (
                        <div className="md:col-span-2">
                          <DevPanel title="Case record" explain="The persisted case row: reason_code, severity and owner are set by the lead agent; dead letters by the queue.">
                            <Json value={{ case_id: c.case_id, kind: c.kind, reason_code: c.reason_code, severity: c.severity, owner: c.owner, thread_id: c.thread_id, injection_detected: c.injection_detected }} />
                          </DevPanel>
                        </div>
                      )}
                    </div>
                  )}
                </Card>
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}
