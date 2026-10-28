import type { RelayEvent } from "../lib/api";

let id = 0;
export const ev = (node: string | null, event_type: string, metadata: Record<string, unknown> = {}, thread = "northwind:NW-T01:g1"): RelayEvent => ({
  event_id: ++id, tenant_id: "northwind", run_id: "r1", thread_id: thread, ticket_id: "NW-T01",
  event_type, node, status: event_type === "node_completed" ? "completed" : "running", duration_ms: 5, metadata, at: "2026-10-28T10:00:00Z",
});
const done = (node: string, m: Record<string, unknown> = {}) => [ev(node, "node_started"), ev(node, "node_completed", m)];

export const draftWaiting: RelayEvent[] = [
  ev("orchestrator", "run_started"),
  ...done("validate_input"), ...done("validate_tenant"), ...done("triage_agent"), ...done("route_decision"),
  ...done("retrieve_knowledge"), ...done("draft_reply"), ...done("safety_check", { passed: true }), ...done("record_decision"),
  ev("await_human_approval", "node_started"), ev("await_human_approval", "approval_required"), ev("await_human_approval", "node_waiting"),
];

export const escalatedEarly: RelayEvent[] = [
  ev("orchestrator", "run_started"),
  ...done("validate_input"), ...done("validate_tenant"), ...done("triage_agent"), ...done("route_decision"),
  ...done("escalate_ticket"), ...done("record_decision"), ...done("finalize_run"),
  ev("finalize_run", "run_finished", { final_status: "escalated" }),
];

export const ticketNotProcessed = {
  ticket: { tenant_id: "northwind", ticket_id: "NW-T01", customer_ref: "C-1001", subject: "Return a helmet",
    body: "Can I return it?", received_at: "2026-10-20T03:42:00Z", source: "data/northwind/tickets.jsonl" },
  decision: null, approvals: [], outbox: null, cases: [], threads: [], model_calls: [], audit: [], events: [],
  view: { state: "not_processed", allowed_actions: ["process"], pending_approval_id: null },
};

export const ticketRows = [
  { tenant_id: "harbor", ticket_id: "HD-T12", subject: "Thank you", state: "not_processed", received_at: "2026-10-21T06:40:00Z" },
  { tenant_id: "northwind", ticket_id: "NW-T12", subject: "Bulk order", state: "escalated", received_at: "2026-10-21T07:35:00Z" },
  { tenant_id: "northwind", ticket_id: "NW-T01", subject: "Return a helmet", state: "waiting_approval", received_at: "2026-10-20T03:42:00Z" },
].map((r) => ({ customer_ref: "c", source: null, category: null, priority: null, action: null, decision_status: null,
  injection_detected: null, latency_ms: null, tokens_in: null, tokens_out: null, reason_code: null, allowed_actions: [], ...r }));

export const baseRoutes = {
  "GET /api/me": { name: "operator", tenants: ["harbor", "northwind"] },
  "GET /api/approvals": [],
  "GET /api/escalations": [],
  "GET /api/tenants": [{ tenant_id: "harbor", name: "Harbor Dental" }, { tenant_id: "northwind", name: "Northwind Cycles" }],
};
