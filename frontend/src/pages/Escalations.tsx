import { useState } from "react";
import { Card, Empty, ErrorBox, Loading, PageHeader, Pill, StatusBadge } from "../components/ui";
import { fmt, useApi } from "../lib/api";

interface CaseRow {
  case_id: string; tenant_id: string; ticket_id: string; thread_id: string | null; kind: string; owner: string;
  severity: string; reason_code: string; reason: string; status: string; created_at: string;
  category: string | null; priority: string | null; injection_detected: boolean | null; subject: string | null;
  next_step: string;
  audit: { actor: string; action: string; from_state: string | null; to_state: string | null; reason: string | null; at: string }[];
}

const SEV: Record<string, string> = { sev1: "text-[#ef6b6b]", sev2: "text-status-serious", sev3: "text-status-warning", sev4: "text-fg-muted" };

export default function Escalations() {
  const cases = useApi<CaseRow[]>("/api/escalations", 8000);
  const [open, setOpen] = useState<string | null>(null);
  return (
    <>
      <PageHeader
        title="Escalation center"
        subtitle="Cases opened by the lead agent (escalations) and by the queue (dead letters). Summaries are written by the system and never include internal KB text."
      />
      {cases.error && <ErrorBox error={cases.error} onRetry={cases.reload} />}
      {!cases.data ? <Loading rows={6} /> : !cases.data.length ? <Empty title="No cases" /> : (
        <div className="space-y-2">
          {cases.data.map((c) => (
            <Card key={c.case_id}>
              <button className="w-full text-left" onClick={() => setOpen(open === c.case_id ? null : c.case_id)} aria-expanded={open === c.case_id}>
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <span className={`mono text-xs font-semibold uppercase ${SEV[c.severity]}`}>{c.severity}</span>
                  <span className="mono text-sm">{c.ticket_id}</span>
                  <span className="text-sm text-fg-faint">{c.tenant_id}</span>
                  <Pill tone={c.reason_code === "injection" ? "critical" : "neutral"}>{fmt.label(c.reason_code)}</Pill>
                  {c.kind === "dead_letter" && <Pill tone="critical">dead letter</Pill>}
                  <span className="text-sm text-fg-muted">→ {c.owner}</span>
                  <span className="ml-auto"><StatusBadge status={c.status} /></span>
                </div>
                <p className="mt-1.5 text-sm">{c.reason}</p>
              </button>
              {open === c.case_id && (
                <div className="mt-3 grid gap-4 border-t border-ink-700 pt-3 md:grid-cols-2">
                  <div>
                    <div className="label mb-1">Next recommended step</div>
                    <p className="text-sm text-fg-muted">{c.next_step}</p>
                    <div className="label mb-1 mt-3">Ticket</div>
                    <p className="text-sm text-fg-muted">“{c.subject}” · {fmt.label(c.category)} · {c.priority} · opened {fmt.datetime(c.created_at)}</p>
                  </div>
                  <div>
                    <div className="label mb-1">Audit history</div>
                    <ol className="space-y-1 text-sm">
                      {c.audit.map((a, i) => (
                        <li key={i} className="text-fg-muted">
                          <span className="mono text-fg-faint">{fmt.time(a.at)}</span> <span className="text-fg">{a.actor}</span> {fmt.label(a.action)}
                          {a.to_state ? ` → ${a.to_state}` : ""}{a.reason ? ` · ${a.reason}` : ""}
                        </li>
                      ))}
                    </ol>
                  </div>
                </div>
              )}
            </Card>
          ))}
        </div>
      )}
    </>
  );
}
