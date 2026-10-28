import { useEffect, useId, useRef, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { explainError, fmt } from "../lib/api";
import { useMode } from "../lib/mode";
import { APPROVAL, STATE, type Meaning, type Tone } from "../lib/status";

export const TONE_TEXT: Record<Tone, string> = {
  neutral: "text-fg-muted", info: "text-series-1", attention: "text-status-warning",
  good: "text-[#3ecf7a]", warning: "text-status-serious", critical: "text-[#ef6b6b]",
};
const TONE_BG: Record<Tone, string> = {
  neutral: "border-ink-600 bg-ink-850", info: "border-series-1/40 bg-series-1/10",
  attention: "border-status-warning/40 bg-status-warning/10", good: "border-accent/40 bg-accent-soft/50",
  warning: "border-status-serious/40 bg-status-serious/10", critical: "border-status-critical/50 bg-status-critical/10",
};

/** Every page starts by saying what it is and what you can do here. */
export function PageHeader({ title, purpose, next, actions }: {
  title: string; purpose: ReactNode; next?: ReactNode; actions?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
      <div className="max-w-3xl">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        <p className="mt-1.5 text-[15px] leading-relaxed text-fg-muted">{purpose}</p>
        {next && <p className="mt-1.5 text-sm text-fg-faint"><span className="font-medium text-fg-muted">What to do: </span>{next}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Card({ title, help, right, children, className = "" }: {
  title?: ReactNode; help?: string; right?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <section className={`card ${className}`}>
      {title && (
        <header className="flex items-center justify-between gap-3 border-b border-ink-700 px-4 py-3">
          <h2 className="flex items-center gap-1.5 text-[15px] font-semibold">{title}{help && <Help text={help} />}</h2>
          {right}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

/** Accessible tooltip: shows on hover and on keyboard focus. */
export function Help({ text }: { text: string }) {
  const id = useId();
  return (
    <span className="group relative inline-flex">
      <button type="button" aria-describedby={id} aria-label="More information"
        className="grid h-4 w-4 place-items-center rounded-full border border-ink-600 text-[10px] font-semibold text-fg-faint hover:border-fg-faint hover:text-fg focus:text-fg">
        ?
      </button>
      <span id={id} role="tooltip"
        className="pointer-events-none absolute left-1/2 top-6 z-30 hidden w-64 -translate-x-1/2 rounded-md border border-ink-600 bg-ink-850 px-3 py-2 text-left text-xs font-normal normal-case leading-relaxed tracking-normal text-fg shadow-xl group-focus-within:block group-hover:block">
        {text}
      </span>
    </span>
  );
}

export function Badge({ meaning, raw }: { meaning: Meaning; raw?: string }) {
  const { isDev } = useMode();
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-md border px-2 py-0.5 text-xs font-medium ${TONE_BG[meaning.tone]} ${TONE_TEXT[meaning.tone]}`}
      title={meaning.help}>
      <span aria-hidden>{meaning.icon}</span>
      {meaning.label}
      {isDev && raw && <code className="ml-1 font-mono text-[10px] opacity-70">{raw}</code>}
    </span>
  );
}

export function StateBadge({ state }: { state: string | null | undefined }) {
  if (!state) return <span className="text-fg-faint">—</span>;
  return <Badge meaning={STATE[state] ?? { label: fmt.label(state), help: "", tone: "neutral", icon: "·" }} raw={state} />;
}

export function ApprovalBadge({ status }: { status: string }) {
  return <Badge meaning={APPROVAL[status] ?? { label: status, help: "", tone: "neutral", icon: "·" }} raw={status} />;
}

/** A number with its definition; links to the matching filtered list when clickable. */
export function Stat({ label, value, help, to, tone }: {
  label: string; value: ReactNode; help: string; to?: string; tone?: Tone;
}) {
  const body = (
    <>
      <div className="flex items-center gap-1.5 text-[13px] font-medium text-fg-muted">{label}</div>
      <div className={`mt-1 text-3xl font-semibold tabular-nums tracking-tight ${tone ? TONE_TEXT[tone] : ""}`}>{value}</div>
      <div className="mt-1 text-xs leading-snug text-fg-faint">{help}</div>
      {to && <div className="mt-2 text-xs font-medium text-accent">View list →</div>}
    </>
  );
  const cls = "card block px-4 py-3.5 text-left";
  return to ? <Link to={to} className={`${cls} transition-colors hover:border-ink-600 hover:bg-ink-850`}>{body}</Link> : <div className={cls}>{body}</div>;
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="space-y-2" aria-busy aria-live="polite">
      <span className="sr-only">{label}</span>
      {[90, 72, 55].map((w) => <div key={w} className="h-4 animate-pulse rounded bg-ink-800" style={{ width: `${w}%` }} />)}
    </div>
  );
}

export function ErrorBox({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-status-critical/40 bg-status-critical/5 px-3 py-2.5 text-sm text-[#f3a0a0]">
      <span><span aria-hidden>✕ </span>{typeof error === "string" ? error : explainError(error)}</span>
      {onRetry && <button className="btn-ghost" onClick={onRetry}>Try again</button>}
    </div>
  );
}

export function Notice({ tone = "info", children }: { tone?: Tone; children: ReactNode }) {
  return <div role="status" className={`rounded-md border px-3 py-2.5 text-sm ${TONE_BG[tone]} ${TONE_TEXT[tone]}`}>{children}</div>;
}

export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-ink-600 px-4 py-10 text-center">
      <div className="text-[15px] font-medium text-fg">{title}</div>
      {children && <div className="mx-auto mt-1.5 max-w-md text-sm leading-relaxed text-fg-muted">{children}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function KV({ rows }: { rows: [ReactNode, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[minmax(7rem,auto)_1fr] gap-x-4 gap-y-2 text-sm">
      {rows.map(([k, v], i) => (
        <div key={i} className="contents">
          <dt className="text-fg-faint">{k}</dt>
          <dd className="min-w-0 break-words">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Json({ value }: { value: unknown }) {
  return (
    <pre className="mono max-h-96 overflow-auto rounded-md border border-ink-700 bg-ink-950 p-3 leading-relaxed text-fg-muted">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

const RULE_TEXT: Record<string, string> = {
  CITE_EXISTS: "Every cited article exists in this company's knowledge base",
  CITE_NOT_INTERNAL: "No staff-only article is cited",
  CITE_RETRIEVED: "Cited articles were the ones found for this ticket",
  CITE_DECLARED: "Article references in the text are recorded",
  NO_INTERNAL_TEXT: "No staff-only text or internal reference codes",
  NO_OTHER_TENANT: "No mention of another company",
  NO_CLINICAL_ADVICE: "No medical advice, diagnosis or medication",
  NO_FOREIGN_IDS: "No other customer's or patient's identifiers",
  NUMBERS_GROUNDED: "Every number comes from the knowledge base or the ticket",
  NO_UNGROUNDED_CONTACTS: "No invented email addresses, links or phone numbers",
  MAX_150_WORDS: "150 words or fewer",
  ASK_HAS_QUESTION: "The message asks the customer a question",
  GROUNDED: "Based on the knowledge base",
  CONTENT_SUPPORTED: "Content matches the cited articles",
  UNCITED_NO_COMMITMENTS: "A short courtesy note with no promises or figures",
};

export function CheckRules({ rules }: { rules: { rule_id: string; passed: boolean; message: string }[] }) {
  const { isDev } = useMode();
  return (
    <ul className="divide-y divide-ink-800 text-sm">
      {rules.map((r) => (
        <li key={r.rule_id} className="flex items-start gap-2.5 py-1.5">
          <span className={r.passed ? "text-[#3ecf7a]" : "text-[#ef6b6b]"} aria-label={r.passed ? "passed" : "failed"}>{r.passed ? "✓" : "✕"}</span>
          <span className="min-w-0">
            <span>{RULE_TEXT[r.rule_id] ?? r.rule_id}</span>
            {isDev && <code className="ml-2 font-mono text-[11px] text-fg-faint">{r.rule_id}</code>}
            {(!r.passed || (isDev && r.message !== "ok")) && <span className="block text-xs text-fg-muted">{r.message}</span>}
          </span>
        </li>
      ))}
    </ul>
  );
}

/** Modal confirmation for consequential actions. Esc or Cancel closes; focus starts on Cancel. */
export function ConfirmDialog({ open, title, children, confirmLabel, tone = "primary", busy, onConfirm, onCancel, confirmDisabled }: {
  open: boolean; title: string; children: ReactNode; confirmLabel: string; tone?: "primary" | "danger";
  busy?: boolean; onConfirm: () => void; onCancel: () => void; confirmDisabled?: boolean;
}) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  useEffect(() => {
    if (!open) return;
    cancelRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && !busy && onCancel();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, busy, onCancel]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4" role="presentation">
      <div role="dialog" aria-modal="true" aria-labelledby={titleId} className="card w-full max-w-lg p-5 shadow-2xl">
        <h2 id={titleId} className="text-lg font-semibold">{title}</h2>
        <div className="mt-3 space-y-3 text-sm leading-relaxed text-fg-muted">{children}</div>
        <div className="mt-5 flex justify-end gap-2">
          <button ref={cancelRef} className="btn-ghost" onClick={onCancel} disabled={busy}>Cancel</button>
          <button className={tone === "danger" ? "btn-danger" : "btn-primary"} onClick={onConfirm} disabled={busy || confirmDisabled}>
            {busy ? "Working…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

/** Developer Mode panel wrapper: visibly technical, with an explanation. */
export function DevPanel({ title, explain, children }: { title: string; explain: string; children: ReactNode }) {
  return (
    <section className="rounded-lg border border-series-1/30 bg-[#0e1520]">
      <header className="border-b border-series-1/20 px-4 py-2.5">
        <h2 className="flex items-center gap-2 font-mono text-[13px] font-semibold text-[#8fbcf2]">
          <span className="rounded bg-series-1/20 px-1.5 py-px text-[10px] tracking-wider">DEV</span>{title}
        </h2>
        <p className="mt-0.5 text-xs text-fg-faint">{explain}</p>
      </header>
      <div className="p-4">{children}</div>
    </section>
  );
}
