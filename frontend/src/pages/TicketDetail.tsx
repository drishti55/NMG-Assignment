import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ModelCalls, NodeTimeline, ProgressTracker } from "../components/Execution";
import {
  ApprovalBadge, Card, CheckRules, ConfirmDialog, DevPanel, ErrorBox, Help, Json, KV, Loading, Notice, StateBadge,
} from "../components/ui";
import { explainError, fmt, post, useApi, type RelayEvent } from "../lib/api";
import { useMode } from "../lib/mode";
import { ACTION, CATEGORY, PRIORITY, STATE, TENANT, TERM, reasonText, sourceLabel } from "../lib/status";
import { useThreadEvents } from "../lib/thread";

interface Approval {
  approval_id: string; attempt: number; kind: string; status: string; draft_text: string; kb_refs: string[];
  decided_by: string | null; decided_at: string | null; reason: string | null; thread_id: string;
  check_result: { passed: boolean; rules: { rule_id: string; passed: boolean; message: string }[] };
}
export interface TicketDetailData {
  ticket: { tenant_id: string; ticket_id: string; customer_ref: string; subject: string; body: string; received_at: string; source: string | null };
  decision: {
    action: string; status: string; category: string; priority: string; injection_detected: boolean; draft_reply: string | null;
    kb_refs: string[]; reason_code: string | null; model: string; tokens_in: number; tokens_out: number; latency_ms: number; run_id: string; thread_id: string;
  } | null;
  approvals: Approval[];
  outbox: { text: string; approved_by: string; approved_at: string; idempotency_key: string } | null;
  cases: { case_id: string; owner: string; severity: string; reason_code: string; reason: string; kind: string }[];
  threads: { thread_id: string; status: string; started_at: string; run_id: string }[];
  model_calls: { task: string; model: string; tokens_in: number | null; tokens_out: number | null; latency_ms: number; ok: boolean; error_class: string | null; at: string; thread_id: string | null }[];
  audit: { actor: string; action: string; from_state: string | null; to_state: string | null; reason: string | null; at: string }[];
  events: RelayEvent[];
  view: { state: string; allowed_actions: string[]; pending_approval_id: string | null };
}

const AUDIT_TEXT: Record<string, string> = {
  decision_recorded: "The system recorded its decision",
  approval_requested: "A reply was sent for human review",
  approval_approved: "The reply was approved",
  approval_rejected: "The reply was rejected",
  outbox_written: "The approved reply was placed in the outbox",
  escalation_case_opened: "A case was opened for a person",
  escalation_case_updated: "The case was updated",
  dead_letter_case_opened: "Processing failed; a case was opened for the operations team",
};

export default function TicketDetail() {
  const { tenant = "", ticketId = "" } = useParams();
  const path = `/api/tickets/${encodeURIComponent(tenant)}/${encodeURIComponent(ticketId)}`;
  const detail = useApi<TicketDetailData>(path);
  const d = detail.data;
  const { isDev, setMode } = useMode();

  // Live updates come from the latest LangGraph thread of this ticket (or the one just started).
  const [startedThread, setStartedThread] = useState<string | null>(null);
  useEffect(() => setStartedThread(null), [path]);
  const latestThread = startedThread ?? d?.threads.at(-1)?.thread_id ?? null;
  const { events: live } = useThreadEvents(latestThread);
  const reload = detail.reload;
  useEffect(() => {
    const last = live.at(-1);
    if (last && ["run_finished", "approval_required", "job_dead_lettered"].includes(last.event_type)) reload();
  }, [live, reload]);
  // Poll only while the backend says work is in progress; never to start anything.
  useEffect(() => {
    if (d?.view.state !== "processing") return;
    const id = setInterval(reload, 3000);
    return () => clearInterval(id);
  }, [d?.view.state, reload]);

  const events = useMemo(() => {
    const byId = new Map<number, RelayEvent>();
    for (const e of [...(d?.events ?? []), ...live]) byId.set(e.event_id, e);
    return [...byId.values()].sort((a, b) => a.event_id - b.event_id);
  }, [d?.events, live]);
  const threadEvents = events.filter((e) => e.thread_id === latestThread);

  if (detail.error) return (
    <div className="space-y-4">
      <BackLink />
      <ErrorBox error={detail.error} onRetry={detail.reload} />
    </div>
  );
  if (!d) return <Loading label="Loading ticket" />;

  const { ticket, decision, approvals, outbox, cases, view } = d;
  const pending = approvals.find((a) => a.approval_id === view.pending_approval_id) ?? null;
  const latestApproval = approvals.at(-1) ?? null;
  const draftText = outbox?.text ?? pending?.draft_text ?? decision?.draft_reply ?? null;
  const draftCheck = pending?.check_result ?? latestApproval?.check_result ?? null;
  const prio = decision ? PRIORITY[decision.priority] : null;

  return (
    <div className="space-y-5">
      <BackLink />
      <header className="card p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold tracking-tight">{ticket.subject || "(no subject)"}</h1>
            <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-fg-muted">
              <span>Ticket <span className="font-mono text-fg">{ticket.ticket_id}</span></span>
              <span>{TENANT[ticket.tenant_id] ?? ticket.tenant_id}</span>
              {prio && <span className="inline-flex items-center gap-1">Priority: <span className="text-fg">{prio.label}</span><Help text={prio.help} /></span>}
              <span>{sourceLabel(ticket.source)}</span>
            </div>
          </div>
          <StateBadge state={view.state} />
        </div>
        <p className="mt-3 text-sm text-fg-muted">{STATE[view.state]?.help}</p>
      </header>

      <Actions data={d} onChanged={(thread) => { if (thread) setStartedThread(thread); reload(); }} />

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="space-y-5">
          <Card title="Customer request">
            <KV rows={[
              ["Received", fmt.datetime(ticket.received_at)],
              ["Customer reference", ticket.customer_ref],
            ]} />
            <p className="mt-3 whitespace-pre-wrap rounded-md border border-ink-700 bg-ink-850 p-3 text-[15px] leading-relaxed">{ticket.body || "(empty message)"}</p>
          </Card>

          <Card title="What the system decided">
            {!decision ? (
              <p className="text-sm text-fg-muted">
                {view.state === "processing" ? "The AI is working on this ticket. The decision will appear here when it is ready." :
                  "No decision yet. The ticket has not been processed."}
              </p>
            ) : (
              <div className="space-y-3">
                {decision.injection_detected && (
                  <Notice tone="critical">
                    <strong>Possible manipulation attempt.</strong> This message contains instructions aimed at the AI system.
                    They were detected and not followed, and the ticket was handed to a person.
                  </Notice>
                )}
                <KV rows={[
                  ["Decision", <span key="a" className="font-medium">{ACTION[decision.action]?.label ?? decision.action}</span>],
                  ["Why", reasonText(decision.reason_code)],
                  ["Topic", CATEGORY[decision.category] ?? decision.category],
                  ...(decision.kb_refs.length ? [["Knowledge used", <span key="k" className="inline-flex flex-wrap gap-1.5">{decision.kb_refs.map((k) => <span key={k} className="rounded border border-accent/40 px-1.5 font-mono text-xs text-accent">{k}</span>)}</span>]] as [string, JSX.Element][] : []),
                ]} />
                {cases.length > 0 && (
                  <p className="text-sm text-fg-muted">
                    A case is open for <strong className="text-fg">{cases[0].owner.replace(/-/g, " ")}</strong>.{" "}
                    <Link className="text-accent hover:underline" to={`/escalations?case=${cases[0].case_id}`}>View why this ticket was escalated →</Link>
                  </p>
                )}
              </div>
            )}
          </Card>

          <Card title={outbox ? "Reply sent to the customer" : "Draft reply"}>
            {draftText ? (
              <div className="space-y-3">
                <p className="whitespace-pre-wrap rounded-md border border-ink-700 bg-ink-850 p-3 text-[15px] leading-relaxed">{draftText}</p>
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-fg-muted">
                  <span>{fmt.words(draftText)} words</span>
                  {draftCheck && <span className={draftCheck.passed ? "text-[#3ecf7a]" : "text-[#ef6b6b]"}>{draftCheck.passed ? "✓ Passed all safety checks" : "✕ Failed a safety check"}</span>}
                  {latestApproval && <ApprovalBadge status={latestApproval.status} />}
                  {outbox && <span>Approved by {outbox.approved_by}, {fmt.datetime(outbox.approved_at)}</span>}
                </div>
                {draftCheck && (
                  <details className="text-sm">
                    <summary className="cursor-pointer text-fg-muted hover:text-fg">Show the safety checks</summary>
                    <div className="mt-2"><CheckRules rules={draftCheck.rules} /></div>
                  </details>
                )}
              </div>
            ) : (
              <p className="text-sm text-fg-muted">
                {view.state === "escalated" ? "No reply was drafted: this ticket was handed to a person." :
                  view.state === "not_processed" ? "No draft yet. Process the ticket to have the AI prepare one." :
                    "No draft is available."}
              </p>
            )}
            {approvals.filter((a) => a.status === "rejected").map((a) => (
              <p key={a.approval_id} className="mt-3 text-sm text-fg-muted">Draft {a.attempt} was rejected by {a.decided_by}: “{a.reason}”</p>
            ))}
          </Card>

          {!isDev && d.audit.length > 0 && (
            <Card title="History">
              <ol className="space-y-1.5 text-sm">
                {d.audit.map((a, i) => (
                  <li key={i} className="flex gap-3">
                    <span className="w-36 shrink-0 text-fg-faint">{fmt.datetime(a.at)}</span>
                    <span>{AUDIT_TEXT[a.action] ?? fmt.label(a.action)}{a.actor && !["lead-agent", "outbox-writer", "queue"].includes(a.actor) ? ` by ${a.actor}` : ""}{a.reason && a.action === "approval_rejected" ? `: “${a.reason}”` : ""}</span>
                  </li>
                ))}
              </ol>
            </Card>
          )}
        </div>

        <aside className="space-y-5">
          <Card title="Progress" help="Each step is marked only when the system recorded that it actually happened.">
            <ProgressTracker events={threadEvents} />
            {latestThread && (
              <div className="mt-4 border-t border-ink-700 pt-3 text-sm">
                {isDev ? <Link className="text-[#8fbcf2] hover:underline" to={`/monitor?thread=${encodeURIComponent(latestThread)}`}>Open in the agent monitor →</Link> :
                  <button className="text-accent hover:underline" onClick={() => setMode("developer")}>Open execution details (Developer Mode) →</button>}
              </div>
            )}
          </Card>
        </aside>
      </div>

      {isDev && (
        <div className="space-y-5">
          <DevPanel title="LangGraph node timeline" explain={`Every event the workflow recorded for thread ${latestThread ?? "(none)"}. Lead nodes are deterministic code; specialist nodes are agents.`}>
            <NodeTimeline events={threadEvents} />
          </DevPanel>
          <DevPanel title="Model calls (gateway meter)" explain="Every call made for this ticket, with token counts reported by Ollama. 'unavailable' means the runtime did not report a value.">
            <ModelCalls calls={d.model_calls} />
          </DevPanel>
          <div className="grid gap-5 lg:grid-cols-2">
            <DevPanel title="Correlation IDs" explain={TERM.correlation}>
              <KV rows={[
                ["threads", <span key="t" className="font-mono text-xs">{d.threads.map((t) => `${t.thread_id} (${t.status})`).join(", ") || "none"}</span>],
                ["run_id", <span key="r" className="font-mono text-xs">{decision?.run_id ?? "none"}</span>],
                ["approvals", <span key="a" className="font-mono text-xs">{approvals.map((a) => `${a.approval_id}#${a.attempt}=${a.status}`).join(", ") || "none"}</span>],
                ["cases", <span key="c" className="font-mono text-xs">{cases.map((c) => `${c.case_id} ${c.kind}/${c.reason_code}`).join(", ") || "none"}</span>],
                ["outbox key", <span key="o" className="font-mono text-xs">{outbox?.idempotency_key ?? "none"}</span>],
                ["state", <span key="s" className="font-mono text-xs">{view.state} → allowed: [{view.allowed_actions.join(", ")}]</span>],
              ]} />
            </DevPanel>
            <DevPanel title="Decision record (decisions.jsonl row)" explain="The persisted decision, as exported to out/decisions.jsonl (plus internal reason code and thread).">
              {decision ? <Json value={decision} /> : <p className="text-sm text-fg-faint">No decision recorded.</p>}
            </DevPanel>
          </div>
          <DevPanel title="Audit log" explain="Append-only record of every state change: who, when, what and why.">
            <Json value={d.audit} />
          </DevPanel>
        </div>
      )}
    </div>
  );
}

function BackLink() {
  return <Link to="/tickets" className="text-sm text-fg-muted hover:text-fg">← All tickets</Link>;
}

/** Only the actions the backend lists as valid for the persisted state. */
function Actions({ data, onChanged }: { data: TicketDetailData; onChanged: (thread?: string) => void }) {
  const { ticket, view, approvals, cases } = data;
  const [dialog, setDialog] = useState<"process" | "approve" | "reject" | null>(null);
  const [reason, setReason] = useState("");
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ tone: "good" | "critical"; text: string } | null>(null);
  const tenantName = TENANT[ticket.tenant_id] ?? ticket.tenant_id;
  const pending = approvals.find((a) => a.approval_id === view.pending_approval_id);
  const reasonOk = reason.trim().length >= 3;
  const can = (a: string) => view.allowed_actions.includes(a);

  async function run(kind: "process" | "approve" | "reject") {
    setBusy(true);
    setResult(null);
    try {
      if (kind === "process") {
        const r = await post<{ thread_id: string }>(`/api/tickets/${ticket.tenant_id}/${encodeURIComponent(ticket.ticket_id)}/process`);
        setResult({ tone: "good", text: "Processing started. Progress updates below as each step finishes (usually 5 to 15 seconds)." });
        onChanged(r.thread_id);
      } else {
        const r = await post<{ changed: boolean; outbox_written: boolean; thread_phase: string }>(
          `/api/approvals/${pending!.approval_id}/${kind}`, kind === "reject" ? { reason: reason.trim() } : undefined);
        setResult({
          tone: "good",
          text: kind === "approve"
            ? r.changed ? "Reply approved and placed in the outbox for the customer." : "This reply had already been approved. Nothing changed."
            : r.thread_phase === "paused" ? "Rejected. The AI is preparing one revised draft using your reason; it will appear here for review."
              : "Rejected. The ticket has been handed to a person (second rejection).",
        });
        onChanged();
      }
      setDialog(null);
      setReason("");
      setTouched(false);
    } catch (e) {
      setResult({ tone: "critical", text: explainError(e) });
      setDialog(null);
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  const none = !can("process") && !can("approve") && !can("view_escalation");
  return (
    <section className="card p-4" aria-label="Actions">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-sm font-semibold">Next step:</span>
        {can("process") && <button className="btn-primary" onClick={() => setDialog("process")}>Process this ticket</button>}
        {can("approve") && pending && <button className="btn-primary" onClick={() => setDialog("approve")}>Approve reply</button>}
        {can("reject") && pending && <button className="btn-danger" onClick={() => setDialog("reject")}>Reject and give a reason</button>}
        {can("view_escalation") && cases[0] && <Link className="btn-ghost" to={`/escalations?case=${cases[0].case_id}`}>View why this ticket was escalated</Link>}
        {none && <span className="text-sm text-fg-muted">
          {view.state === "processing" ? "Nothing to do while the AI is working. This page updates by itself." :
            view.state === "sent" ? "Nothing to do. The reply was approved and sent." : "No action is available for this ticket."}
        </span>}
      </div>
      {result && <div className="mt-3"><Notice tone={result.tone}>{result.text}</Notice></div>}

      <ConfirmDialog open={dialog === "process"} title="Process this ticket?" confirmLabel="Yes, process this ticket" busy={busy}
        onConfirm={() => run("process")} onCancel={() => setDialog(null)}>
        <p>You are about to process exactly one ticket:</p>
        <p className="rounded-md border border-ink-700 bg-ink-850 p-3 text-fg">
          <span className="font-mono">{ticket.ticket_id}</span> · {tenantName}<br />“{ticket.subject}”
        </p>
        <p>The AI will classify it, check the knowledge base and either prepare a reply or hand it to a person.
          It uses the local AI models and takes about 5 to 15 seconds. <strong className="text-fg">Nothing is sent to the customer without your approval.</strong></p>
      </ConfirmDialog>

      <ConfirmDialog open={dialog === "approve"} title="Approve and send this reply?" confirmLabel="Approve reply" busy={busy}
        onConfirm={() => run("approve")} onCancel={() => setDialog(null)}>
        <p>This places the reply in the outbox for the customer of <span className="font-mono">{ticket.ticket_id}</span> ({tenantName}). It cannot be undone.</p>
        <p className="whitespace-pre-wrap rounded-md border border-ink-700 bg-ink-850 p-3 text-fg">{pending?.draft_text}</p>
        <p>Your name is recorded as the approver.</p>
      </ConfirmDialog>

      <ConfirmDialog open={dialog === "reject"} title="Reject this reply" confirmLabel="Reject reply" tone="danger" busy={busy}
        confirmDisabled={!reasonOk} onConfirm={() => run("reject")} onCancel={() => setDialog(null)}>
        <p>Tell the AI what is wrong. It gets <strong className="text-fg">one</strong> chance to revise using your reason; a second rejection hands the ticket to a person.</p>
        <label className="block text-fg" htmlFor="reject-reason">Reason (required)</label>
        <textarea id="reject-reason" className="input min-h-[90px] w-full" value={reason} onChange={(e) => setReason(e.target.value)}
          onBlur={() => setTouched(true)} aria-invalid={touched && !reasonOk} aria-describedby="reason-hint"
          placeholder="e.g. Pune is a metro city: quote 4 to 6 business days" />
        <p id="reason-hint" className={touched && !reasonOk ? "text-[#ef6b6b]" : "text-fg-faint"}>
          {touched && !reasonOk ? "Please write at least 3 characters so the AI knows what to fix." : "At least 3 characters."}
        </p>
      </ConfirmDialog>
    </section>
  );
}

