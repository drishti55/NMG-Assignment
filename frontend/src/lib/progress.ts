import type { RelayEvent } from "./api";

/** Simple-view progress, derived only from execution events the backend recorded.
 * A stage is "done" only if the node that performs it completed. */

export type StageStatus = "done" | "current" | "waiting" | "skipped" | "failed" | "todo";

export interface Stage {
  key: string;
  label: string;
  status: StageStatus;
  note?: string;
}

const STAGES: { key: string; label: string; nodes: string[] }[] = [
  { key: "received", label: "Ticket received", nodes: ["validate_input"] },
  { key: "understood", label: "Ticket understood", nodes: ["triage_agent"] },
  { key: "knowledge", label: "Knowledge base checked", nodes: ["retrieve_knowledge"] },
  { key: "drafted", label: "Reply prepared", nodes: ["draft_reply"] },
  { key: "checked", label: "Safety checks completed", nodes: ["safety_check"] },
  { key: "approval", label: "Human approval", nodes: ["await_human_approval"] },
  { key: "sent", label: "Reply sent", nodes: ["write_approved_outbox"] },
];

export interface Progress {
  stages: Stage[];
  started: boolean;
  outcome: "none" | "running" | "waiting_approval" | "sent" | "escalated" | "failed" | "held" | "finished";
}

export function deriveProgress(events: RelayEvent[]): Progress {
  const completed = new Set<string>();
  const started = new Set<string>();
  const failed = new Set<string>();
  let waiting = false;
  let finished = false;
  let finalStatus: string | null = null;
  let escalated = false;
  let checkFailedLast = false;
  for (const e of events) {
    if (!e.node) continue;
    if (e.event_type === "node_started") started.add(e.node);
    if (e.event_type === "node_completed") {
      completed.add(e.node);
      failed.delete(e.node);
      if (e.node === "escalate_ticket") escalated = true;
      if (e.node === "safety_check") checkFailedLast = e.metadata.passed === false;
    }
    if (e.event_type === "node_failed") failed.add(e.node);
    if (e.event_type === "node_waiting") waiting = true;
    if (e.event_type === "run_resumed" && e.node === "await_human_approval") waiting = false;
    if (e.event_type === "run_finished") {
      finished = true;
      finalStatus = String(e.metadata.final_status ?? "");
    }
  }
  if (!events.some((e) => e.event_type === "run_started" || e.node === "validate_input")) {
    return { stages: STAGES.map((s) => ({ key: s.key, label: s.label, status: "todo" })), started: false, outcome: "none" };
  }

  const stopReason = escalated
    ? "Skipped: the ticket was handed to a person instead."
    : finalStatus === "held_budget"
      ? "Skipped: the usage limit was reached."
      : finalStatus === "failed"
        ? "Skipped: processing failed."
        : "Not needed for this ticket.";

  const stages: Stage[] = STAGES.map((s) => {
    const node = s.nodes[0];
    if (failed.has(node)) return { key: s.key, label: s.label, status: "failed", note: "This step hit an error; it will be retried." };
    if (s.key === "approval") {
      if (waiting && !finished) return { key: s.key, label: s.label, status: "waiting", note: "Waiting for a person to approve or reject the reply." };
      if (completed.has(node)) return { key: s.key, label: s.label, status: "done" };
    }
    if (s.key === "checked" && completed.has(node) && checkFailedLast && !completed.has("record_decision"))
      return { key: s.key, label: s.label, status: "current", note: "A check failed; the AI is revising the reply." };
    if (completed.has(node)) return { key: s.key, label: s.label, status: "done" };
    if (started.has(node)) return { key: s.key, label: s.label, status: "current" };
    return { key: s.key, label: s.label, status: finished ? "skipped" : "todo", note: finished ? stopReason : undefined };
  });

  let outcome: Progress["outcome"] = "running";
  if (completed.has("write_approved_outbox")) outcome = "sent";
  else if (waiting && !finished) outcome = "waiting_approval";
  else if (escalated) outcome = "escalated";
  else if (finalStatus === "held_budget") outcome = "held";
  else if (finalStatus === "failed") outcome = "failed";
  else if (finished) outcome = "finished";
  return { stages, started: true, outcome };
}
