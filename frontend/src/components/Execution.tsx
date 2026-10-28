import { useState } from "react";
import { fmt, type RelayEvent } from "../lib/api";
import { deriveProgress, type StageStatus } from "../lib/progress";
import { NODE, TERM } from "../lib/status";
import { CheckRules, Help, Json } from "./ui";

const STAGE_STYLE: Record<StageStatus, { icon: string; cls: string; text: string }> = {
  done: { icon: "✓", cls: "border-accent bg-accent-soft text-accent", text: "Done" },
  current: { icon: "●", cls: "border-series-1 bg-series-1/15 text-series-1", text: "In progress" },
  waiting: { icon: "◷", cls: "border-status-warning bg-status-warning/15 text-status-warning", text: "Waiting for you" },
  skipped: { icon: "–", cls: "border-ink-600 bg-ink-850 text-fg-faint", text: "Skipped" },
  failed: { icon: "✕", cls: "border-status-critical bg-status-critical/15 text-[#ef6b6b]", text: "Problem" },
  todo: { icon: "", cls: "border-ink-600 bg-ink-900 text-fg-faint", text: "Not started" },
};

/** User Mode: plain stages, each marked only from recorded events. */
export function ProgressTracker({ events }: { events: RelayEvent[] }) {
  const p = deriveProgress(events);
  if (!p.started) return <p className="text-sm text-fg-muted">Processing has not started for this ticket, so there is no progress to show.</p>;
  return (
    <ol className="space-y-0">
      {p.stages.map((s, i) => {
        const st = STAGE_STYLE[s.status];
        return (
          <li key={s.key} className="relative flex gap-3 pb-4 last:pb-0">
            {i < p.stages.length - 1 && <span className="absolute left-[13px] top-7 h-[calc(100%-1.5rem)] w-px bg-ink-700" aria-hidden />}
            <span className={`z-10 grid h-7 w-7 shrink-0 place-items-center rounded-full border text-xs font-semibold ${st.cls}`} aria-hidden>{st.icon}</span>
            <div className="pt-0.5">
              <div className={`text-sm font-medium ${s.status === "skipped" || s.status === "todo" ? "text-fg-faint" : "text-fg"}`}>
                {s.label} <span className="ml-1 text-xs font-normal text-fg-faint">· {st.text}</span>
              </div>
              {s.note && <div className="mt-0.5 text-xs text-fg-muted">{s.note}</div>}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

const LEAD = new Set(["validate_input", "validate_tenant", "route_decision", "request_information", "repair_invalid_output",
  "escalate_ticket", "record_decision", "await_human_approval", "prepare_redraft", "write_approved_outbox", "record_failure",
  "finalize_run", "checker_failed", "rejected_twice", "orchestrator", "queue"]);

/** Developer Mode: every recorded event, with node explanations and typed output summaries. */
export function NodeTimeline({ events }: { events: RelayEvent[] }) {
  const [raw, setRaw] = useState<number | null>(null);
  if (!events.length) return <p className="text-sm text-fg-faint">No execution events recorded for this ticket.</p>;
  return (
    <ol className="space-y-1.5">
      {events.filter((e) => e.event_type !== "node_started").map((e) => {
        const m = e.metadata as Record<string, unknown>;
        const lead = LEAD.has(e.node ?? "");
        return (
          <li key={e.event_id} className={`rounded-md border px-3 py-2 ${lead ? "border-ink-700 bg-ink-900" : "ml-5 border-series-1/40 bg-[#0e1520]"}`}>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
              <span className="font-mono text-xs text-fg-faint">#{e.event_id} {fmt.time(e.at)}</span>
              <code className="font-mono text-[13px] font-semibold">{e.node}</code>
              <span className={`rounded px-1.5 text-[10px] font-semibold uppercase tracking-wider ${lead ? "bg-ink-700 text-fg-muted" : "bg-series-1/25 text-[#a9cdf7]"}`}>
                {lead ? "lead (code)" : "specialist"}
              </span>
              <span className="font-mono text-xs text-fg-muted">{e.event_type}</span>
              {typeof m.contract === "string" && <span className="font-mono text-xs text-[#a9cdf7]">→ {m.contract}</span>}
              <span className="ml-auto flex items-center gap-3 font-mono text-xs">
                {e.duration_ms != null && <span className="text-fg-faint">{fmt.ms(e.duration_ms)}</span>}
                <span className={e.status === "failed" ? "text-[#ef6b6b]" : e.status === "completed" ? "text-[#3ecf7a]" : "text-status-warning"}>{e.status}</span>
                <button className="text-fg-faint underline-offset-2 hover:text-fg hover:underline" onClick={() => setRaw(raw === e.event_id ? null : e.event_id)}>
                  {raw === e.event_id ? "hide raw" : "raw"}
                </button>
              </span>
            </div>
            {e.node && NODE[e.node] && <p className="mt-0.5 text-xs text-fg-faint">{NODE[e.node]}</p>}
            <EventDetail m={m} />
            {raw === e.event_id && <div className="mt-2"><Json value={e} /></div>}
          </li>
        );
      })}
    </ol>
  );
}

function EventDetail({ m }: { m: Record<string, unknown> }) {
  const parts: JSX.Element[] = [];
  if (typeof m.model === "string")
    parts.push(
      <div key="m" className="flex flex-wrap items-center gap-x-3 font-mono text-xs text-fg-muted">
        <span>model {m.model}</span>
        <span className="inline-flex items-center gap-1">in {String(m.tokens_in ?? "unavailable")} tok <Help text={TERM.tokens_in} /></span>
        <span className="inline-flex items-center gap-1">out {String(m.tokens_out ?? "unavailable")} tok <Help text={TERM.tokens_out} /></span>
        <span>latency {fmt.ms(Number(m.model_latency_ms))}</span>
      </div>,
    );
  if (typeof m.category === "string" && typeof m.proposed_action === "string")
    parts.push(<div key="t" className="font-mono text-xs text-fg-muted">TriageResult: category={m.category} priority={String(m.priority)} proposed_action={m.proposed_action} model_injection_flag={String(m.model_injection_flag)}</div>);
  if (typeof m.reason_code === "string")
    parts.push(
      <div key="r" className="font-mono text-xs text-fg-muted">
        RouteDecision: action={String(m.action)} reason={m.reason_code}
        {Array.isArray(m.overrides) && m.overrides.length > 0 && <span className="text-status-warning"> overrides=[{(m.overrides as string[]).join(", ")}]</span>}
        {typeof m.injection_score === "number" && <> injection_score={m.injection_score} signals=[{(m.injection_signals as string[]).join(", ")}]</>}
      </div>,
    );
  if (Array.isArray(m.passages))
    parts.push(
      <div key="p" className="flex flex-wrap items-center gap-1.5 font-mono text-xs text-fg-muted">
        retrieved: {(m.passages as { kb_id: string; score: number }[]).map((p) => (
          <span key={p.kb_id} className="rounded border border-ink-600 px-1.5">{p.kb_id} sim={p.score.toFixed(3)}</span>
        ))}
        <Help text={TERM.similarity} />
        {m.adequate === false && <span className="text-status-warning">no adequate evidence</span>}
      </div>,
    );
  if (Array.isArray(m.rules)) parts.push(<div key="c" className="mt-1"><CheckRules rules={m.rules as { rule_id: string; passed: boolean; message: string }[]} /></div>);
  if (typeof m.error_class === "string") parts.push(<div key="e" className="font-mono text-xs text-[#ef6b6b]">error_class={m.error_class}{m.transient ? " (transient: will retry)" : ""}</div>);
  if (typeof m.approval_id === "string") parts.push(<div key="a" className="font-mono text-xs text-fg-muted">approval_id={m.approval_id}{typeof m.decision === "string" ? ` decision=${m.decision}` : ""}{m.from_checkpoint ? " (resumed from checkpoint)" : ""}</div>);
  if (typeof m.attempt === "number" && typeof m.worker === "string") parts.push(<div key="q" className="font-mono text-xs text-fg-muted">queue lease: worker={m.worker} attempt={m.attempt} lease={String(m.lease_seconds)}s</div>);
  return parts.length ? <div className="mt-1.5 space-y-1">{parts}</div> : null;
}

export function ModelCalls({ calls }: { calls: { task: string; model: string; tokens_in: number | null; tokens_out: number | null; latency_ms: number; ok: boolean; error_class: string | null; at: string }[] }) {
  if (!calls.length) return <p className="text-sm text-fg-faint">No model calls recorded.</p>;
  const sum = (k: "tokens_in" | "tokens_out") => calls.reduce((a, c) => a + (c[k] ?? 0), 0);
  return (
    <div className="overflow-x-auto">
      <table className="w-full font-mono text-xs">
        <thead className="text-left text-fg-faint">
          <tr>
            <th className="pb-2 font-medium">time</th><th className="pb-2 font-medium">task</th><th className="pb-2 font-medium">model</th>
            <th className="pb-2 font-medium"><span className="inline-flex items-center gap-1">tokens in <Help text={TERM.tokens_in} /></span></th>
            <th className="pb-2 font-medium"><span className="inline-flex items-center gap-1">tokens out <Help text={TERM.tokens_out} /></span></th>
            <th className="pb-2 font-medium">latency</th><th className="pb-2 font-medium">result</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-ink-800 tabular-nums">
          {calls.map((c, i) => (
            <tr key={i}>
              <td className="py-1.5 text-fg-faint">{fmt.time(c.at)}</td><td>{c.task}</td><td>{c.model}</td>
              <td>{c.tokens_in ?? <span className="text-fg-faint">unavailable</span>}</td>
              <td>{c.tokens_out ?? <span className="text-fg-faint">unavailable</span>}</td>
              <td>{fmt.ms(c.latency_ms)}</td>
              <td>{c.ok ? <span className="text-[#3ecf7a]">ok</span> : <span className="text-[#ef6b6b]">{c.error_class}</span>}</td>
            </tr>
          ))}
          <tr className="text-fg"><td className="py-1.5" colSpan={3}>total ({calls.length} calls)</td><td>{sum("tokens_in")}</td><td>{sum("tokens_out")}</td><td colSpan={2} /></tr>
        </tbody>
      </table>
    </div>
  );
}
