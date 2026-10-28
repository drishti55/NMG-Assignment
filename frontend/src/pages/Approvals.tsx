import { useState } from "react";
import { Card, CheckRules, Empty, ErrorBox, KV, Loading, PageHeader, Pill, StatusBadge } from "../components/ui";
import { api, fmt, useApi } from "../lib/api";

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
  const [tab, setTab] = useState<"pending" | "all">("pending");
  const list = useApi<Approval[]>(`/api/approvals?status=${tab}`, 6000);
  const [sel, setSel] = useState<string | null>(null);
  const current = sel ?? list.data?.[0]?.approval_id ?? null;

  return (
    <>
      <PageHeader
        title="Approval center"
        subtitle="Nothing reaches a customer without a human decision. Approval writes the outbox exactly once; a rejection needs a reason and triggers one redraft."
        actions={
          <div className="flex rounded-md border border-ink-600 p-0.5 text-sm">
            {(["pending", "all"] as const).map((t) => (
              <button key={t} onClick={() => { setTab(t); setSel(null); }}
                className={`rounded px-3 py-1 ${tab === t ? "bg-ink-800 text-fg" : "text-fg-muted"}`}>{t === "pending" ? "Pending" : "History"}</button>
            ))}
          </div>
        }
      />
      {list.error && <ErrorBox error={list.error} onRetry={list.reload} />}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,360px)_1fr]">
        <Card>
          {!list.data ? <Loading rows={6} /> : !list.data.length ? (
            <Empty title={tab === "pending" ? "Nothing waiting for approval" : "No approvals yet"} />
          ) : (
            <ul className="-m-2 space-y-1">
              {list.data.map((a) => (
                <li key={a.approval_id}>
                  <button onClick={() => setSel(a.approval_id)}
                    className={`w-full rounded-md px-3 py-2 text-left ${current === a.approval_id ? "bg-ink-800" : "hover:bg-ink-850"}`}>
                    <div className="flex items-center justify-between gap-2 text-sm">
                      <span><span className="mono">{a.ticket_id}</span> <span className="text-fg-faint">· {a.tenant_id}</span></span>
                      <StatusBadge status={a.status} />
                    </div>
                    <div className="mt-0.5 flex gap-1.5 text-[11px] text-fg-faint">
                      <span>{fmt.label(a.kind)}</span>·<span>attempt {a.attempt}</span>·<span>{a.priority ?? "—"}</span>
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
        {current ? <ApprovalPanel key={current} id={current} onDone={list.reload} /> : <Card><Empty title="Select an approval" /></Card>}
      </div>
    </>
  );
}

function ApprovalPanel({ id, onDone }: { id: string; onDone: () => void }) {
  const d = useApi<Detail>(`/api/approvals/${id}`);
  const [busy, setBusy] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [result, setResult] = useState<string>();
  const [error, setError] = useState<string>();

  async function decide(kind: "approve" | "reject") {
    setBusy(true);
    setError(undefined);
    try {
      const out = await api<{ status: string; changed: boolean; outbox_written: boolean; thread_phase: string }>(
        `/api/approvals/${id}/${kind}`,
        { method: "POST", body: kind === "reject" ? JSON.stringify({ reason }) : undefined },
      );
      setResult(
        kind === "approve"
          ? `Approved${out.changed ? "" : " (already approved, no change)"}. Outbox written: ${out.outbox_written ? "yes" : "no"}.`
          : out.thread_phase === "paused"
            ? "Rejected. The drafter produced a revised draft; it is now waiting for approval."
            : "Rejected. The ticket has been escalated to a human (second rejection or failed redraft).",
      );
      setRejecting(false);
      d.reload();
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (d.error) return <ErrorBox error={d.error} onRetry={d.reload} />;
  if (!d.data) return <Card><Loading rows={8} /></Card>;
  const { approval: a, ticket, citations, outbox } = d.data;
  const pending = a.status === "pending";
  return (
    <div className="space-y-4">
      <Card title={`${a.ticket_id} · ${fmt.label(a.kind)}`} right={<StatusBadge status={a.status} />}>
        <div className="label mb-1">Customer wrote</div>
        <div className="text-sm font-medium">{ticket.subject}</div>
        <p className="mt-1 whitespace-pre-wrap text-sm text-fg-muted">{ticket.body}</p>
        <div className="label mb-1 mt-4">Proposed reply</div>
        <p className="whitespace-pre-wrap rounded-md border border-ink-700 bg-ink-850 p-3 text-sm leading-relaxed">{a.draft_text}</p>
        <div className="mt-2 flex flex-wrap gap-1.5">{a.kb_refs.map((k) => <Pill key={k} tone="accent">{k}</Pill>)}</div>

        {result && <div className="mt-4 rounded-md border border-accent/40 bg-accent-soft/40 px-3 py-2 text-sm text-accent">✓ {result}</div>}
        {error && <div className="mt-4"><ErrorBox error={error} /></div>}

        {pending && !result && (
          <div className="mt-4 border-t border-ink-700 pt-4">
            {!a.check_result.passed && <div className="mb-3"><ErrorBox error="Checker failed: this draft cannot be approved." /></div>}
            {rejecting ? (
              <div className="space-y-2">
                <label className="label" htmlFor="reason">Reason (required, shown to the drafter for one revision)</label>
                <textarea id="reason" className="input min-h-[80px] w-full" value={reason} onChange={(e) => setReason(e.target.value)}
                  placeholder="e.g. Pune is a metro city: quote 4 to 6 business days" />
                <div className="flex gap-2">
                  <button className="btn-danger" disabled={busy || reason.trim().length < 3} onClick={() => decide("reject")}>
                    {busy ? "Rejecting… (redrafting can take a few seconds)" : "Confirm rejection"}
                  </button>
                  <button className="btn-ghost" disabled={busy} onClick={() => setRejecting(false)}>Cancel</button>
                </div>
              </div>
            ) : (
              <div className="flex gap-2">
                <button className="btn-primary" disabled={busy || !a.check_result.passed} onClick={() => decide("approve")}>
                  {busy ? "Approving…" : "Approve and send"}
                </button>
                <button className="btn-ghost" disabled={busy} onClick={() => setRejecting(true)}>Reject…</button>
              </div>
            )}
          </div>
        )}
        {!pending && (
          <div className="mt-4 border-t border-ink-700 pt-4">
            <KV rows={[
              ["Decided by", a.decided_by ?? "—"],
              ["At", fmt.datetime(a.decided_at)],
              ...(a.reason ? ([["Reason", a.reason]] as [string, string][]) : []),
              ["Outbox", outbox ? `written once · key ${outbox.idempotency_key.slice(0, 12)}…` : "not written"],
            ]} />
          </div>
        )}
      </Card>
      <Card title="Permitted citations">
        {citations.length ? citations.map((c) => (
          <div key={c.kb_id} className="mb-3 last:mb-0">
            <div className="text-sm"><span className="mono text-accent">{c.kb_id}</span> {c.title}</div>
            <p className="text-sm text-fg-muted">{c.body}</p>
          </div>
        )) : <p className="text-sm text-fg-faint">This draft cites no KB article.</p>}
      </Card>
      <Card title={`Checker · ${a.check_result.passed ? "passed" : "failed"}`}>
        <CheckRules rules={a.check_result.rules} />
      </Card>
    </div>
  );
}
