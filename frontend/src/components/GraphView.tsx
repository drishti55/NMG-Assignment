import { useMemo } from "react";
import { fmt, type RelayEvent } from "../lib/api";

export interface GraphShape {
  nodes: string[];
  edges: { source: string; target: string; conditional: boolean }[];
}

export type NodeState = "idle" | "running" | "completed" | "waiting" | "retrying" | "failed" | "skipped";

// Screen positions only (column, row). Which nodes and edges exist comes from the backend.
const POS: Record<string, [number, number]> = {
  __start__: [1, 0], validate_input: [1, 1], validate_tenant: [1, 2], record_failure: [0, 2.5],
  triage_agent: [1, 3], route_decision: [1, 4], repair_invalid_output: [0, 4.6],
  retrieve_knowledge: [1, 5], request_information: [2, 5.6], draft_reply: [1, 6.2],
  safety_check: [1, 7.2], checker_failed: [2, 7.2], record_decision: [1, 8.2],
  escalate_ticket: [2, 8.2], prepare_redraft: [0, 8.8], await_human_approval: [1, 9.2],
  rejected_twice: [2, 9.6], write_approved_outbox: [1, 10.2], finalize_run: [1, 11.2], __end__: [1, 12.2],
};
const ROLE: Record<string, "lead" | "agent" | "check" | "human" | "io"> = {
  triage_agent: "agent", retrieve_knowledge: "agent", draft_reply: "agent", safety_check: "check",
  await_human_approval: "human", write_approved_outbox: "io", __start__: "io", __end__: "io",
};
const ROLE_LABEL = { lead: "lead", agent: "specialist", check: "checker", human: "human gate", io: "" };

const W = 176, H = 34, COLW = 196, ROWH = 52, PADX = 12, PADY = 14;

function center(name: string, extra: number): [number, number] {
  const [c, r] = POS[name] ?? [3, extra];
  return [PADX + c * COLW + W / 2, PADY + r * ROWH + H / 2];
}

/** Clip a line from p toward q so it ends on q's box edge (arrowheads stay visible). */
function clip(p: [number, number], q: [number, number]): [number, number] {
  const dx = q[0] - p[0], dy = q[1] - p[1];
  const t = Math.min(dx ? (W / 2 + 4) / Math.abs(dx) : Infinity, dy ? (H / 2 + 4) / Math.abs(dy) : Infinity);
  return [q[0] - dx * t, q[1] - dy * t];
}

export function nodeStates(events: RelayEvent[]): { state: Record<string, NodeState>; runs: Record<string, number>; durations: Record<string, number>; path: [string, string][] } {
  const state: Record<string, NodeState> = {}, runs: Record<string, number> = {}, durations: Record<string, number> = {};
  const order: string[] = [];
  let finished = false, lastFailed: string | null = null;
  for (const e of events) {
    const n = e.node;
    if (e.event_type === "run_finished") finished = true;
    if (!n) continue;
    if (e.event_type === "node_started") {
      state[n] = "running";
      runs[n] = (runs[n] ?? 0) + 1;
      order.push(n);
    } else if (e.event_type === "node_completed") {
      state[n] = "completed";
      durations[n] = (durations[n] ?? 0) + (e.duration_ms ?? 0);
    } else if (e.event_type === "node_waiting") state[n] = "waiting";
    else if (e.event_type === "node_failed") {
      state[n] = "failed";
      lastFailed = n;
    } else if (e.event_type === "job_retry_scheduled" && lastFailed) state[lastFailed] = "retrying";
    else if (e.event_type === "run_resumed" && n === "await_human_approval") state[n] = "completed";
  }
  if (order.length) state.__start__ = "completed";
  if (finished) state.__end__ = "completed";
  const path: [string, string][] = order.length ? [["__start__", order[0]]] : [];
  for (let i = 1; i < order.length; i++) path.push([order[i - 1], order[i]]);
  if (finished && order.length) path.push([order[order.length - 1], "__end__"]);
  return { state, runs, durations, path };
}

const FILL: Record<NodeState, string> = {
  idle: "#151816", running: "#12301f", completed: "#16211b", waiting: "#2d2510", retrying: "#2e1d14",
  failed: "#2d1414", skipped: "#111312",
};
const STROKE: Record<NodeState, string> = {
  idle: "#343a37", running: "#3ecf7a", completed: "#2f7a4f", waiting: "#fab219", retrying: "#ec835a",
  failed: "#d03b3b", skipped: "#272c29",
};
const ICON: Record<NodeState, string> = {
  idle: "", running: "●", completed: "✓", waiting: "◷", retrying: "↻", failed: "✕", skipped: "–",
};

export function GraphView({ graph, events, selected, onSelect, finished }: {
  graph: GraphShape; events: RelayEvent[]; selected?: string | null; onSelect?: (n: string) => void; finished: boolean;
}) {
  const { state, runs, durations, path } = useMemo(() => nodeStates(events), [events]);
  const taken = new Set(path.map(([a, b]) => `${a}>${b}`));
  const unknown = graph.nodes.filter((n) => !POS[n]);
  const extraRow = (n: string) => 13 + unknown.indexOf(n);
  const width = PADX * 2 + COLW * 2 + W;
  const height = PADY * 2 + ROWH * (12.2 + unknown.length) + H;

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full max-w-[620px]" role="img" aria-label="LangGraph workflow">
      <defs>
        {["#3a403d", "#3ecf7a"].map((c, i) => (
          <marker key={c} id={`arrow${i}`} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M0,0 L8,4 L0,8 z" fill={c} />
          </marker>
        ))}
      </defs>
      {graph.edges.map((e) => {
        const p = center(e.source, extraRow(e.source)), q = center(e.target, extraRow(e.target));
        const end = clip(p, q), start = clip(q, p);
        const hot = taken.has(`${e.source}>${e.target}`);
        const bend = p[0] === q[0] && q[1] < p[1] ? 60 : 0; // loops back up curve outward
        const d = bend
          ? `M${start[0] + W / 2 - 4},${p[1]} C${p[0] + W / 2 + bend},${p[1]} ${q[0] + W / 2 + bend},${q[1]} ${q[0] + W / 2 + 4},${q[1]}`
          : `M${start[0]},${start[1]} L${end[0]},${end[1]}`;
        return (
          <path key={`${e.source}>${e.target}`} d={d} fill="none" stroke={hot ? "#3ecf7a" : "#3a403d"}
            strokeWidth={hot ? 2 : 1.25} strokeDasharray={e.conditional && !hot ? "4 3" : undefined}
            markerEnd={`url(#arrow${hot ? 1 : 0})`} opacity={hot ? 1 : 0.35} />
        );
      })}
      {graph.nodes.map((n) => {
        const [x, y] = center(n, extraRow(n));
        const s: NodeState = state[n] ?? (finished ? "skipped" : "idle");
        const role = ROLE[n] ?? "lead";
        const isIO = n.startsWith("__");
        return (
          <g key={n} transform={`translate(${x - W / 2},${y - H / 2})`} className={onSelect && !isIO ? "cursor-pointer" : ""}
            onClick={() => !isIO && onSelect?.(n)}>
            <title>{`${n} — ${s}${durations[n] != null ? `, ${fmt.ms(durations[n])}` : ""}${runs[n] > 1 ? `, ran ${runs[n]}×` : ""}`}</title>
            <rect width={W} height={H} rx={isIO ? 17 : 6} fill={FILL[s]} stroke={selected === n ? "#e7ebe8" : STROKE[s]}
              strokeWidth={selected === n ? 1.5 : 1} />
            {s === "running" && <rect width={W} height={H} rx={6} fill="none" stroke="#3ecf7a" opacity={0.5}>
              <animate attributeName="opacity" values="0.6;0.1;0.6" dur="1.4s" repeatCount="indefinite" />
            </rect>}
            <text x={10} y={14} fontSize={11} fill={s === "skipped" ? "#4f5652" : "#e7ebe8"} fontFamily="JetBrains Mono, ui-monospace, monospace">
              {isIO ? n.replace(/_/g, "") : n}
            </text>
            <text x={10} y={27} fontSize={9.5} fill="#6e7671">
              {ROLE_LABEL[role]}{durations[n] != null ? `${ROLE_LABEL[role] ? " · " : ""}${fmt.ms(durations[n])}` : ""}
              {runs[n] > 1 ? ` · ×${runs[n]}` : ""}
            </text>
            {ICON[s] && <text x={W - 16} y={21} fontSize={12} fill={STROKE[s]} textAnchor="middle">{ICON[s]}</text>}
          </g>
        );
      })}
    </svg>
  );
}

export function Legend() {
  const items: NodeState[] = ["idle", "running", "completed", "waiting", "retrying", "failed", "skipped"];
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-fg-muted">
      {items.map((s) => (
        <span key={s} className="inline-flex items-center gap-1.5">
          <span className="inline-block h-3 w-3 rounded-sm border" style={{ background: FILL[s], borderColor: STROKE[s] }} />
          {ICON[s] && <span style={{ color: STROKE[s] }} aria-hidden>{ICON[s]}</span>}
          {s === "waiting" ? "waiting for approval" : s}
        </span>
      ))}
      <span className="inline-flex items-center gap-1.5">
        <svg width="22" height="6"><line x1="0" y1="3" x2="22" y2="3" stroke="#3a403d" strokeDasharray="4 3" /></svg>
        conditional edge
      </span>
      <span className="inline-flex items-center gap-1.5">
        <svg width="22" height="6"><line x1="0" y1="3" x2="22" y2="3" stroke="#3ecf7a" strokeWidth="2" /></svg>
        path taken
      </span>
    </div>
  );
}
