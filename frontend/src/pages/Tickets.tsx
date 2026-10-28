import { useState } from "react";
import { Link } from "react-router-dom";
import { Card, CheckRules, Empty, ErrorBox, Json, KV, Loading, PageHeader, Pill, StatusBadge } from "../components/ui";
import { fmt, useApi, type RelayEvent } from "../lib/api";
import { useDevMode } from "../lib/devmode";

interface Row {
  tenant_id: string; ticket_id: string; customer_ref: string; subject: string; received_at: string;
  category: string | null; priority: string | null; action: string | null; status: string | null;
  injection_detected: boolean | null; latency_ms: number | null; tokens_in: number | null; tokens_out: number | null;
  approval_status: string | null; sent: boolean;
}

const CATEGORIES = ["returns_refunds", "shipping_delivery", "warranty_repair", "order_change", "billing_payments",
  "assembly_setup", "appointments", "records_privacy", "clinical", "other"];

export default function Tickets() {
  const tenants = useApi<{ tenant_id: string; name: string }[]>("/api/tenants");
  const [f, setF] = useState({ tenant: "", q: "", category: "", priority: "", action: "", status: "" });
  const [sel, setSel] = useState<Row | null>(null);
  const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v)).toString();
  const rows = useApi<Row[]>(`/api/tickets${qs ? `?${qs}` : ""}`, 8000);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });

  return (
    <>
      <PageHeader title="Ticket explorer" subtitle="Every ticket the queue has accepted, with its decision. Filters run server-side inside each tenant's RLS scope." />
      <div className="mb-4 flex flex-wrap gap-2">
        <input className="input w-56" placeholder="Search id, subject, body" value={f.q} onChange={set("q")} />
        <select className="input" value={f.tenant} onChange={set("tenant")}>
          <option value="">All my tenants</option>
          {tenants.data?.map((t) => <option key={t.tenant_id} value={t.tenant_id}>{t.name}</option>)}
        </select>
        <select className="input" value={f.category} onChange={set("category")}>
          <option value="">Any category</option>
          {CATEGORIES.map((c) => <option key={c} value={c}>{fmt.label(c)}</option>)}
        </select>
        <select className="input" value={f.priority} onChange={set("priority")}>
          <option value="">Any priority</option>
          {["urgent", "high", "normal", "low"].map((p) => <option key={p}>{p}</option>)}
        </select>
        <select className="input" value={f.action} onChange={set("action")}>
          <option value="">Any action</option>
          {["draft_reply", "ask_for_info", "escalate"].map((a) => <option key={a} value={a}>{fmt.label(a)}</option>)}
        </select>
        <select className="input" value={f.status} onChange={set("status")}>
          <option value="">Any status</option>
          {["pending_approval", "escalated", "held_budget", "failed"].map((s) => <option key={s} value={s}>{fmt.label(s)}</option>)}
        </select>
      </div>
      {rows.error && <ErrorBox error={rows.error} onRetry={rows.reload} />}
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,520px)]">
        <Card>
          {!rows.data ? <Loading rows={8} /> : !rows.data.length ? <Empty title="No tickets match" /> : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-fg-faint">
                  <tr>{["Ticket", "Subject", "Category", "Pri.", "Action", "Status", "Latency"].map((h) => <th key={h} className="pb-2 pr-3 font-medium">{h}</th>)}</tr>
                </thead>
                <tbody className="divide-y divide-ink-800">
                  {rows.data.map((r) => (
                    <tr key={r.tenant_id + r.ticket_id} onClick={() => setSel(r)}
                      className={`cursor-pointer hover:bg-ink-850 ${sel?.ticket_id === r.ticket_id && sel.tenant_id === r.tenant_id ? "bg-ink-850" : ""}`}>
                      <td className="py-1.5 pr-3"><span className="mono">{r.ticket_id}</span><div className="text-[11px] text-fg-faint">{r.tenant_id}</div></td>
                      <td className="max-w-[16rem] truncate pr-3">{r.subject}{r.injection_detected && <span className="ml-2"><Pill tone="critical">injection</Pill></span>}</td>
                      <td className="pr-3 text-fg-muted">{fmt.label(r.category)}</td>
                      <td className="pr-3 text-fg-muted">{r.priority ?? "—"}</td>
                      <td className="pr-3">{fmt.label(r.action)}</td>
                      <td className="pr-3"><StatusBadge status={r.sent ? "sent" : r.approval_status === "pending" ? "pending_approval" : r.status} /></td>
                      <td className="mono text-fg-faint">{fmt.ms(r.latency_ms)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
        {sel ? <TicketPanel tenant={sel.tenant_id} id={sel.ticket_id} /> : (
          <Card><Empty title="Select a ticket">Details, retrieved KB references, draft, checker results and run history appear here.</Empty></Card>
        )}
      </div>
    </>
  );
}

interface Detail {
  ticket: { body: string; subject: string; customer_ref: string; received_at: string };
  decision: { action: string; status: string; category: string; priority: string; draft_reply: string | null; kb_refs: string[]; model: string; tokens_in: number; tokens_out: number; latency_ms: number; reason_code: string | null; run_id: string } | null;
  approvals: { approval_id: string; attempt: number; status: string; decided_by: string | null; decided_at: string | null; reason: string | null; draft_text: string; check_result: { rules: { rule_id: string; passed: boolean; message: string }[] } }[];
  outbox: { approved_by: string; approved_at: string } | null;
  cases: { case_id: string; owner: string; severity: string; reason_code: string; reason: string }[];
  threads: { thread_id: string; status: string; started_at: string }[];
  model_calls: { task: string; model: string; tokens_in: number | null; tokens_out: number | null; latency_ms: number; ok: boolean }[];
  audit: { actor: string; action: string; from_state: string | null; to_state: string | null; reason: string | null; at: string }[];
  events: RelayEvent[];
}

function TicketPanel({ tenant, id }: { tenant: string; id: string }) {
  const d = useApi<Detail>(`/api/tickets/${tenant}/${encodeURIComponent(id)}`, 6000);
  const { on: dev } = useDevMode();
  if (d.error) return <ErrorBox error={d.error} onRetry={d.reload} />;
  if (!d.data) return <Card><Loading rows={6} /></Card>;
  const { ticket, decision, approvals, outbox, cases, threads, model_calls, audit, events } = d.data;
  const retrieval = [...events].reverse().find((e) => e.node === "retrieve_knowledge" && e.event_type === "node_completed");
  const passages = (retrieval?.metadata.passages ?? []) as { kb_id: string; score: number }[];
  const lastApproval = approvals.at(-1);
  return (
    <div className="space-y-4">
      <Card title={`${id} · ${tenant}`} right={<StatusBadge status={outbox ? "sent" : decision?.status} />}>
        <div className="text-sm font-medium">{ticket.subject}</div>
        <p className="mt-1 whitespace-pre-wrap text-sm text-fg-muted">{ticket.body}</p>
        <div className="mt-3">
          <KV rows={[
            ["Customer", ticket.customer_ref],
            ["Received", fmt.datetime(ticket.received_at)],
            ...(decision ? ([
              ["Decision", `${fmt.label(decision.action)} · ${fmt.label(decision.category)} · ${decision.priority}`],
              ["Reason", <span key="r" className="mono">{decision.reason_code ?? "—"}</span>],
              ["Usage", `${decision.model} · ${fmt.int(decision.tokens_in)} in / ${fmt.int(decision.tokens_out)} out · ${fmt.ms(decision.latency_ms)}`],
            ] as [string, React.ReactNode][]) : []),
            ["Outbox", outbox ? `sent, approved by ${outbox.approved_by} at ${fmt.datetime(outbox.approved_at)}` : "not sent"],
          ]} />
        </div>
      </Card>
      <Card title="Retrieved KB references">
        {passages.length ? (
          <div className="flex flex-wrap gap-1.5">{passages.map((p) => <Pill key={p.kb_id} tone={decision?.kb_refs.includes(p.kb_id) ? "accent" : "neutral"}>{p.kb_id} · {p.score.toFixed(3)}{decision?.kb_refs.includes(p.kb_id) ? " · cited" : ""}</Pill>)}</div>
        ) : <p className="text-sm text-fg-faint">No retrieval for this ticket (escalated before retrieval, or not yet processed).</p>}
      </Card>
      <Card title="Draft">
        {decision?.draft_reply ? <p className="whitespace-pre-wrap text-sm leading-relaxed">{decision.draft_reply}</p>
          : <p className="text-sm text-fg-faint">No customer draft.</p>}
        {lastApproval && <div className="mt-3"><div className="label mb-1">Checker (attempt {lastApproval.attempt})</div><CheckRules rules={lastApproval.check_result.rules} /></div>}
        {lastApproval?.status === "pending" && <Link to="/approvals" className="mt-3 inline-block text-sm text-accent hover:underline">Review in Approval Center →</Link>}
      </Card>
      {cases.length > 0 && (
        <Card title="Cases">
          {cases.map((c) => <div key={c.case_id} className="text-sm"><span className="mono">{c.case_id}</span> · {c.severity} · {c.owner}<div className="text-fg-muted">{c.reason}</div></div>)}
        </Card>
      )}
      <Card title="Run history">
        <ul className="space-y-1 text-sm">
          {threads.map((t) => (
            <li key={t.thread_id} className="flex items-center justify-between gap-2">
              <Link className="mono hover:text-accent" to={`/inspector?thread=${encodeURIComponent(t.thread_id)}`}>{t.thread_id}</Link>
              <StatusBadge status={t.status} />
            </li>
          ))}
        </ul>
        <div className="label mb-1 mt-3">Audit trail</div>
        <ol className="space-y-1 text-sm">
          {audit.map((a, i) => (
            <li key={i} className="text-fg-muted"><span className="mono text-fg-faint">{fmt.time(a.at)}</span> <span className="text-fg">{a.actor}</span> {fmt.label(a.action)}{a.to_state ? ` → ${a.to_state}` : ""}{a.reason ? ` · “${a.reason}”` : ""}</li>
          ))}
        </ol>
        {dev && <div className="mt-3"><div className="label mb-1">Model calls</div><Json value={model_calls} /></div>}
      </Card>
    </div>
  );
}
