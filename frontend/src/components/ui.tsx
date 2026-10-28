import type { ReactNode } from "react";
import { fmt } from "../lib/api";

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 max-w-2xl text-sm text-fg-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Card({ title, right, children, className = "" }: {
  title?: string; right?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <section className={`card ${className}`}>
      {title && (
        <header className="flex items-center justify-between border-b border-ink-700 px-4 py-2.5">
          <h2 className="text-sm font-medium">{title}</h2>
          {right}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Stat({ label, value, hint, tone }: {
  label: string; value: ReactNode; hint?: ReactNode; tone?: "good" | "warning" | "critical";
}) {
  const bar = { good: "bg-status-good", warning: "bg-status-warning", critical: "bg-status-critical" };
  return (
    <div className="card relative overflow-hidden px-4 py-3">
      {tone && <span className={`absolute inset-y-0 left-0 w-0.5 ${bar[tone]}`} aria-hidden />}
      <div className="label">{label}</div>
      <div className="mt-1.5 text-2xl font-semibold tabular-nums tracking-tight">{value}</div>
      {hint && <div className="mt-0.5 text-xs text-fg-faint">{hint}</div>}
    </div>
  );
}

const STATUS: Record<string, { color: string; icon: string; text: string }> = {
  pending_approval: { color: "text-status-warning", icon: "◷", text: "pending approval" },
  pending: { color: "text-status-warning", icon: "◷", text: "pending" },
  waiting_for_approval: { color: "text-status-warning", icon: "◷", text: "waiting for approval" },
  awaiting_approval: { color: "text-status-warning", icon: "◷", text: "awaiting approval" },
  approved: { color: "text-status-good", icon: "✓", text: "approved" },
  completed: { color: "text-status-good", icon: "✓", text: "completed" },
  done: { color: "text-status-good", icon: "✓", text: "done" },
  sent: { color: "text-status-good", icon: "✓", text: "sent" },
  escalated: { color: "text-status-serious", icon: "▲", text: "escalated" },
  rejected: { color: "text-status-serious", icon: "✕", text: "rejected" },
  retrying: { color: "text-status-serious", icon: "↻", text: "retrying" },
  held_budget: { color: "text-status-serious", icon: "⏸", text: "held (budget)" },
  failed: { color: "text-[#ef6b6b]", icon: "✕", text: "failed" },
  dead: { color: "text-[#ef6b6b]", icon: "✕", text: "dead letter" },
  running: { color: "text-accent", icon: "●", text: "running" },
  leased: { color: "text-accent", icon: "●", text: "leased" },
  queued: { color: "text-fg-muted", icon: "○", text: "queued" },
  open: { color: "text-status-serious", icon: "▲", text: "open" },
};

/** Status is always icon + label, never color alone. */
export function StatusBadge({ status }: { status: string | null | undefined }) {
  if (!status) return <span className="text-fg-faint">—</span>;
  const s = STATUS[status] ?? { color: "text-fg-muted", icon: "·", text: fmt.label(status) };
  return (
    <span className={`inline-flex items-center gap-1 whitespace-nowrap text-xs font-medium ${s.color}`}>
      <span aria-hidden>{s.icon}</span>
      {s.text}
    </span>
  );
}

export function Pill({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "accent" | "critical" }) {
  const cls = {
    neutral: "border-ink-600 text-fg-muted",
    accent: "border-accent/40 text-accent",
    critical: "border-status-critical/50 text-[#f08a8a]",
  }[tone];
  return <span className={`inline-flex items-center rounded border px-1.5 py-px text-[11px] ${cls}`}>{children}</span>;
}

export function Loading({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-2" aria-busy>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="h-4 animate-pulse rounded bg-ink-800" style={{ width: `${90 - i * 15}%` }} />
      ))}
    </div>
  );
}

export function ErrorBox({ error, onRetry }: { error: string; onRetry?: () => void }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-md border border-status-critical/40 bg-status-critical/5 px-3 py-2 text-sm text-[#f08a8a]">
      <span>
        <span aria-hidden>✕ </span>
        {error}
      </span>
      {onRetry && <button className="btn-ghost" onClick={onRetry}>Retry</button>}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-md border border-dashed border-ink-600 px-4 py-8 text-center">
      <div className="text-sm font-medium text-fg-muted">{title}</div>
      {children && <div className="mt-1 text-xs text-fg-faint">{children}</div>}
    </div>
  );
}

/** Horizontal bars, one series. Every bar is direct-labelled with its value; hover shows a tooltip. */
export function BarList({ items, color = "bg-accent-strong", format = fmt.int }: {
  items: { label: string; value: number; hint?: string }[];
  color?: string;
  format?: (n: number) => string;
}) {
  if (!items.length) return <Empty title="No data yet" />;
  const max = Math.max(...items.map((i) => i.value), 1);
  return (
    <ul className="space-y-2">
      {items.map((i) => (
        <li key={i.label} className="group relative grid grid-cols-[minmax(7rem,10rem)_1fr_auto] items-center gap-3 text-sm">
          <span className="truncate text-fg-muted">{fmt.label(i.label)}</span>
          <span className="h-2 rounded-r bg-ink-800">
            <span className={`block h-2 rounded-r ${color}`} style={{ width: `${(i.value / max) * 100}%` }} />
          </span>
          <span className="tabular-nums text-fg">{format(i.value)}</span>
          <span role="tooltip" className="pointer-events-none absolute -top-8 left-40 z-10 hidden rounded border border-ink-600 bg-ink-850 px-2 py-1 text-xs text-fg shadow-lg group-hover:block">
            {fmt.label(i.label)}: {format(i.value)}{i.hint ? ` · ${i.hint}` : ""}
          </span>
        </li>
      ))}
    </ul>
  );
}

export function KV({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-fg-faint">{k}</dt>
          <dd className="min-w-0 break-words">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Json({ value }: { value: unknown }) {
  return (
    <pre className="mono max-h-80 overflow-auto rounded-md border border-ink-700 bg-ink-950 p-3 leading-relaxed text-fg-muted">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

export function CheckRules({ rules }: { rules: { rule_id: string; passed: boolean; message: string }[] }) {
  return (
    <ul className="divide-y divide-ink-700 text-sm">
      {rules.map((r) => (
        <li key={r.rule_id} className="flex items-start gap-2 py-1.5">
          <span className={r.passed ? "text-status-good" : "text-[#ef6b6b]"} aria-hidden>{r.passed ? "✓" : "✕"}</span>
          <span className="mono w-48 shrink-0 text-fg">{r.rule_id}</span>
          <span className="text-fg-muted">{r.passed ? (r.message === "ok" ? "pass" : r.message) : r.message}</span>
        </li>
      ))}
    </ul>
  );
}
