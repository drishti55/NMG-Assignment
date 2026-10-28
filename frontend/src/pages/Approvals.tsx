import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ApprovalBadge, Card, CheckRules, ConfirmDialog, DevPanel, Empty, ErrorBox, Json, KV, Loading, Notice, PageHeader } from "../components/ui";
import { explainError, fmt, post, useApi } from "../lib/api";
import { useMode } from "../lib/mode";
import { ACTION, PRIORITY, TENANT } from "../lib/status";

interface Approval {
  approval_id: string; tenant_id: string; ticket_id: string; thread_id: string; attempt: number; kind: string;
  draft_text: string; kb_refs: string[]; status: string; decided_by: string | null; decided_at: string | null;
  reason: string | null; created_at: string; category: string | null; priority: string | null; outbox_written: boolean;
  check_result: { passed: boolean; rules: { rule_id: string; passed: boolean; message: string }[] };
}
interface Detail {
  approval: Approval;
  ticket: { subject: string; body: string; customer_ref: string };
  citations: { kb_id: string; title: string; body: string }[];
  outbox: { approved_by: string; approved_at: string; idempotency_key: string } | null;
}

export default function Approvals() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "history" ? "all" : "pending";
  const selected = params.get("id");
  const list = useApi<Approval[]>(`/api/approvals?status=${tab}`, 10000);
  const select = (id: string | null) => {
    const next = new URLSearchParams(params);
    if (id) next.set("id", id); else next.delete("id");
    setParams(next);
  };

  return (
    <>
      <PageHeader
        title="Pending approvals"
        purpose="Replies the AI has prepared for customers. Nothing is sent until a person approves it here (or on the ticket's page)."
        next="Pick a reply on the left, read it against the customer's message and the cited policy, then approve it or reject it with a reason."
        actions={
          <div className="flex rounded-md border border-ink-600 p-0.5 text-sm" role="tablist">
            {(["pending", "all"] as const).map((t) => (
              <button key={t} role="tab" aria-selected={tab === t}
                onClick={() => setParams(t === "all" ? { tab: "history" } : {})}
                className={`rounded px-3 py-1.5 ${tab === t ? "bg-ink-800 text-fg" : "text-fg-muted hover:text-fg"}`}>
                {t === "pending" ? "Waiting for review" : "All decisions"}
              </button>
            ))}
          </div>
        }
      />
      {list.error && <ErrorBox error={list.error} onRetry={list.reload} />}
      <div className="grid gap-5 lg:grid-cols-[minmax(0,360px)_1fr]">
        <Card>
          {!list.data ? <Loading /> : !list.data.length ? (
            <Empty title={tab === "pending" ? "Nothing is waiting for approval" : "No decisions yet"}>
              {tab === "pending" ? "When the AI prepares a reply, it appears here for review." : "Approved and rejected replies will be listed here."}
            </Empty>
          ) : (
            <ul className="-m-2 space-y-1" aria-label="Replies">
              {list.data.map((a) => (
                <li key={a.approval_id}>
                  <button onClick={() => select(a.approval_id)} aria-current={selected === a.approval_id}
                    className={`w-full rounded-md px-3 py-2.5 text-left ${selected === a.approval_id ? "bg-ink-800 ring-1 ring-accent/40" : "hover:bg-ink-850"}`}>
                    <div className="flex items-center justify-between gap-2 text-sm">
                      <span><span className="font-mono">{a.ticket_id}</span> <span className="text-fg-faint">· {TENANT[a.tenant_id] ?? a.tenant_id}</span></span>
                      <ApprovalBadge status={a.status} />
                    </div>
                    <div className="mt-0.5 text-xs text-fg-muted">
                      {ACTION[a.kind]?.label ?? a.kind}{a.attempt > 1 ? " · revised draft" : ""}{a.priority ? ` · ${PRIORITY[a.priority]?.label} priority` : ""}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
        {selected ? <ApprovalPanel key={selected} id={selected} onDone={list.reload} /> : (
          <Card><Empty title="Select a reply to review">Choose an item on the left. Nothing is selected automatically.</Empty></Card>
        )}
      </div>
    </>
  );
}

function ApprovalPanel({ id, onDone }: { id: string; onDone: () => void }) {
  const d = useApi<Detail>(`/api/approvals/${id}`);
  const { isDev } = useMode();
  const [dialog, setDialog] = useState<"approve" | "reject" | null>(null);
  const [reason, setReason] = useState("");
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ tone: "good" | "critical"; text: string } | null>(null);
  const reasonOk = reason.trim().length >= 3;

  async function decide(kind: "approve" | "reject") {
    setBusy(true);
    try {
      const out = await post<{ changed: boolean; outbox_written: boolean; thread_phase: string }>(
        `/api/approvals/${id}/${kind}`, kind === "reject" ? { reason: reason.trim() } : undefined);
      setResult({
        tone: "good",
        text: kind === "approve"
          ? out.changed ? "Approved. The reply was placed in the outbox for the customer (exactly once)." : "This reply had already been approved. Nothing changed."
          : out.thread_phase === "paused" ? "Rejected. The AI prepared one revised draft using your reason; it is now in the list as a revised draft."
            : "Rejected. This was the second rejection, so the ticket was handed to a person.",
      });
    } catch (e) {
      setResult({ tone: "critical", text: explainError(e) });
    } finally {
      setBusy(false);
      setDialog(null);
      d.reload();
      onDone();
    }
  }

  if (d.error) return <ErrorBox error={d.error} onRetry={d.reload} />;
  if (!d.data) return <Card><Loading /></Card>;
  const { approval: a, ticket, citations, outbox } = d.data;
  const pending = a.status === "pending";
  return (
    <div className="space-y-5">
      <Card title={<>Reply for <span className="font-mono">{a.ticket_id}</span></>} right={<ApprovalBadge status={a.status} />}>
        <div className="label mb-1">The customer wrote</div>
        <div className="font-medium">{ticket.subject}</div>
        <p className="mt-1 whitespace-pre-wrap text-sm text-fg-muted">{ticket.body}</p>
        <div className="label mb-1 mt-5">Proposed reply ({fmt.words(a.draft_text)} words)</div>
        <p className="whitespace-pre-wrap rounded-md border border-ink-700 bg-ink-850 p-3 text-[15px] leading-relaxed">{a.draft_text}</p>
        <p className={`mt-2 text-sm ${a.check_result.passed ? "text-[#3ecf7a]" : "text-[#ef6b6b]"}`}>
          {a.check_result.passed ? "✓ Passed all safety checks. Still read it: the checks cannot judge whether the meaning is right." : "✕ Failed a safety check, so it cannot be approved."}
        </p>
        {result && <div className="mt-4"><Notice tone={result.tone}>{result.text}</Notice></div>}
        {pending && (
          <div className="mt-4 flex flex-wrap gap-2 border-t border-ink-700 pt-4">
            <button className="btn-primary" disabled={busy || !a.check_result.passed} onClick={() => setDialog("approve")}>Approve reply</button>
            <button className="btn-danger" disabled={busy} onClick={() => setDialog("reject")}>Reject and give a reason</button>
            <Link className="btn-ghost" to={`/tickets/${a.tenant_id}/${encodeURIComponent(a.ticket_id)}`}>Open the full ticket</Link>
          </div>
        )}
        {!pending && (
          <div className="mt-4 border-t border-ink-700 pt-4">
            <KV rows={[
              ["Decided by", a.decided_by ?? "—"], ["When", fmt.datetime(a.decided_at)],
              ...(a.reason ? [["Reason", a.reason]] as [string, string][] : []),
              ["Sent to customer", outbox ? "Yes (written to the outbox once)" : "No"],
            ]} />
          </div>
        )}
      </Card>
      <Card title="Policy the reply is based on" help="Only articles the customer may see are listed. Staff-only articles are never cited.">
        {citations.length ? citations.map((c) => (
          <div key={c.kb_id} className="mb-3 last:mb-0">
            <div className="text-sm font-medium"><span className="mr-2 font-mono text-accent">{c.kb_id}</span>{c.title}</div>
            <p className="text-sm text-fg-muted">{c.body}</p>
          </div>
        )) : <p className="text-sm text-fg-muted">This reply cites no knowledge-base article (for example a short thank-you note).</p>}
      </Card>
      <Card title="Safety checks"><CheckRules rules={a.check_result.rules} /></Card>
      {isDev && (
        <DevPanel title="Approval record" explain="The persisted approval row. The outbox write is guarded by unique constraints on the ticket and the idempotency key.">
          <Json value={{ ...a, outbox }} />
        </DevPanel>
      )}

      <ConfirmDialog open={dialog === "approve"} title="Approve and send this reply?" confirmLabel="Approve reply" busy={busy}
        onConfirm={() => decide("approve")} onCancel={() => setDialog(null)}>
        <p>The reply to <span className="font-mono text-fg">{a.ticket_id}</span> ({TENANT[a.tenant_id]}) goes to the outbox for the customer. This cannot be undone, and your name is recorded.</p>
      </ConfirmDialog>
      <ConfirmDialog open={dialog === "reject"} title="Reject this reply" confirmLabel="Reject reply" tone="danger" busy={busy}
        confirmDisabled={!reasonOk} onConfirm={() => decide("reject")} onCancel={() => setDialog(null)}>
        <p>{a.attempt === 1 ? "The AI gets one chance to revise it using your reason." : "This is already a revised draft: rejecting it hands the ticket to a person."}</p>
        <label className="block text-fg" htmlFor="appr-reason">Reason (required)</label>
        <textarea id="appr-reason" className="input min-h-[90px] w-full" value={reason} onChange={(e) => setReason(e.target.value)}
          onBlur={() => setTouched(true)} aria-invalid={touched && !reasonOk} aria-describedby="appr-hint" />
        <p id="appr-hint" className={touched && !reasonOk ? "text-[#ef6b6b]" : "text-fg-faint"}>
          {touched && !reasonOk ? "Please write at least 3 characters so the AI knows what to fix." : "At least 3 characters."}
        </p>
      </ConfirmDialog>
    </div>
  );
}
