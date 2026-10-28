/** Plain-language meaning of every value the backend returns. One place, so every page
 * says the same thing. Raw values are shown only in Developer Mode. */

export type Tone = "neutral" | "info" | "attention" | "good" | "warning" | "critical";

export interface Meaning {
  label: string;
  help: string;
  tone: Tone;
  icon: string;
}

/** Ticket state, derived by the backend from persisted records (relay/ticket_view.py). */
export const STATE: Record<string, Meaning> = {
  not_processed: {
    label: "Not processed yet",
    help: "The ticket has been received but the AI has not looked at it. Nothing happens until someone chooses to process it.",
    tone: "neutral", icon: "○",
  },
  processing: {
    label: "Being processed",
    help: "The AI is working on this ticket right now, or is preparing a revised reply.",
    tone: "info", icon: "◌",
  },
  waiting_approval: {
    label: "Waiting for your approval",
    help: "The AI has prepared a reply. Nothing is sent to the customer until a person approves it.",
    tone: "attention", icon: "◷",
  },
  sent: {
    label: "Reply approved and sent",
    help: "A person approved the reply and it was placed in the outbox for the customer, exactly once.",
    tone: "good", icon: "✓",
  },
  escalated: {
    label: "Needs human attention",
    help: "The system stopped automatic handling because a person must deal with this case. No reply was drafted.",
    tone: "warning", icon: "▲",
  },
  held_budget: {
    label: "Paused: usage limit reached",
    help: "The AI usage limit for this ticket or run was reached, so the ticket was set aside safely. It can be processed again later.",
    tone: "warning", icon: "⏸",
  },
  failed: {
    label: "Could not be processed",
    help: "Something went wrong (for example the AI service was unavailable). A support case was opened. You can try processing it again.",
    tone: "critical", icon: "✕",
  },
};

export const APPROVAL: Record<string, Meaning> = {
  pending: { label: "Waiting for review", help: "No one has approved or rejected this reply yet.", tone: "attention", icon: "◷" },
  approved: { label: "Approved", help: "A person approved this reply.", tone: "good", icon: "✓" },
  rejected: { label: "Rejected", help: "A person rejected this reply and gave a reason.", tone: "warning", icon: "✕" },
};

/** What the AI decided to do with the ticket. */
export const ACTION: Record<string, { label: string; help: string }> = {
  draft_reply: { label: "Reply to the customer", help: "The knowledge base answers the question, so the AI drafted a reply." },
  ask_for_info: { label: "Ask the customer for more details", help: "A required detail is missing, so the AI drafted one question back." },
  escalate: { label: "Hand over to a person", help: "This needs human judgement, so nothing was drafted." },
};

/** Why the decision was made (reason codes recorded by the lead agent). */
export const REASON: Record<string, string> = {
  injection: "The message contains instructions aimed at the AI system (a possible manipulation attempt). They were not followed, and a person must review it.",
  clinical: "This is a medical or clinical question. The assistant never gives clinical advice, so it goes to the on-duty dentist.",
  legal_threat: "The customer mentions legal action, so a support lead handles it personally.",
  money_dispute: "Money is disputed (for example an overcharge or a missing refund), so the billing team must review it.",
  kb_not_covered: "The company's knowledge base does not cover this request, so the AI could not answer it reliably.",
  checker_failed: "The AI's drafts did not pass the safety checks twice, so a person should write the reply.",
  rejected_twice: "A reviewer rejected the draft twice, so a person will write the reply.",
  empty_ticket: "The ticket has no subject or message.",
  consent_required: "The request concerns another person's records, so the AI asks for their written consent first.",
  model_proposed: "The AI judged that the knowledge base answers this question.",
  model_proposed_escalation: "The AI judged that this needs a person.",
  dead_letter: "Processing failed repeatedly, so the ticket was set aside for the operations team.",
  budget_exceeded: "The AI usage limit was reached before the ticket was finished.",
};

export function reasonText(code: string | null | undefined): string {
  if (!code) return "No reason recorded.";
  const base = code.split(":")[0];
  if (base === "invalid_model_output") return "The AI returned unusable output twice, so the ticket was set aside.";
  return REASON[base] ?? "The system recorded a reason that has no plain-language description yet.";
}

export const CATEGORY: Record<string, string> = {
  returns_refunds: "Returns and refunds", shipping_delivery: "Shipping and delivery",
  warranty_repair: "Warranty and repairs", order_change: "Changing an order",
  billing_payments: "Billing and payments", assembly_setup: "Assembly and setup",
  appointments: "Appointments", records_privacy: "Records and privacy",
  clinical: "Clinical question", other: "Other",
};

export const PRIORITY: Record<string, { label: string; help: string }> = {
  urgent: { label: "Urgent", help: "A safety risk or a legal threat." },
  high: { label: "High", help: "Money at risk, a time-sensitive change or a security concern." },
  normal: { label: "Normal", help: "An ordinary service request." },
  low: { label: "Low", help: "A general information question." },
};

export const TENANT: Record<string, string> = { northwind: "Northwind Cycles", harbor: "Harbor Dental" };

export function sourceLabel(source: string | null | undefined): string {
  if (!source) return "Source not recorded";
  if (source.startsWith("data/")) return `Seed data from the assessment (${source})`;
  if (source === "api" || source.startsWith("api:") || source.startsWith("console:")) return "Submitted through the console";
  return `Imported from ${source}`;
}

/** Short explanations shown next to technical terms in Developer Mode. */
export const TERM: Record<string, string> = {
  thread: "One LangGraph execution for one ticket. Its state is saved after every step so it can pause and resume.",
  run: "One batch of tickets processed together (for example one inbox file).",
  node: "One step of the workflow graph (an agent or a rule).",
  checkpoint: "A saved copy of the workflow state after a step, stored in Postgres so work survives restarts.",
  interrupt: "The workflow pauses here and waits for a person; it resumes from the saved checkpoint.",
  tokens_in: "Tokens are pieces of text processed by the local AI model. Input tokens = text sent to the model.",
  tokens_out: "Output tokens = text the model generated.",
  latency: "How long the step or model call took.",
  similarity: "How close the ticket's meaning is to the knowledge-base article (cosine similarity, 0 to 1).",
  contract: "The typed data structure (Pydantic model) one step hands to the next.",
  lease: "A queue worker's temporary claim on a job. If the worker dies, the lease expires and another worker takes over.",
  correlation: "Identifiers that tie together every record of the same execution.",
  override: "Where the deterministic lead agent changed the model's proposal, always towards safety.",
};

/** What each graph node does, for Developer Mode. */
export const NODE: Record<string, string> = {
  validate_input: "Loads the ticket and checks it is well formed.",
  validate_tenant: "Confirms the company exists; unknown companies are rejected.",
  triage_agent: "Small model classifies the ticket: category, priority, action, injection flag.",
  route_decision: "Deterministic rules turn the model's proposal into a decision.",
  retrieve_knowledge: "Searches this company's knowledge base (vector + keyword).",
  request_information: "Fixes which details the question back must ask for.",
  draft_reply: "Larger model writes the reply from the retrieved passages only.",
  safety_check: "Deterministic rules check the draft (no model).",
  repair_invalid_output: "One bounded retry with the error as feedback.",
  checker_failed: "Draft failed the checks twice; hand over to a person.",
  escalate_ticket: "Opens a case for a person; drafts nothing.",
  record_failure: "Records a classified failure.",
  record_decision: "The lead agent writes the decision record.",
  await_human_approval: "Pauses until a person approves or rejects.",
  prepare_redraft: "After a rejection, prepares one revision with the reviewer's reason.",
  rejected_twice: "Second rejection; hand over to a person.",
  write_approved_outbox: "Writes the approved reply to the outbox exactly once.",
  finalize_run: "Marks the execution as finished.",
};
