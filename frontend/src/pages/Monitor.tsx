import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { GraphView, Legend, nodeStates, type GraphShape } from "../components/GraphView";
import { Card, Empty, ErrorBox, Json, Loading, PageHeader, StatusBadge } from "../components/ui";
import { fmt, useApi, useEventStream } from "../lib/api";
import { useDevMode } from "../lib/devmode";
import { useThreadEvents, type WorkflowRow } from "../lib/thread";

export default function Monitor() {
  const graph = useApi<GraphShape>("/api/graph");
  const runs = useApi<WorkflowRow[]>("/api/runs?limit=40", 4000);
  const [thread, setThread] = useState<string | null>(null);
  const [follow, setFollow] = useState(true);
  const [node, setNode] = useState<string | null>(null);
  const { on: dev } = useDevMode();

  // Follow mode: jump to whichever thread is executing right now.
  useEventStream(follow ? "/api/stream?after=-1" : null, (e) => {
    if (e.thread_id && e.event_type === "run_started") setThread(e.thread_id);
  });
  useEffect(() => {
    if (!thread && runs.data?.length) setThread(runs.data[0].thread_id);
  }, [runs.data, thread]);

  const { events, stream } = useThreadEvents(thread);
  const finished = events.some((e) => e.event_type === "run_finished");
  const waiting = !finished && events.at(-1)?.event_type === "node_waiting";
  const { state } = nodeStates(events);
  const nodeEvents = events.filter((e) => e.node === node);
  const wf = runs.data?.find((r) => r.thread_id === thread);

  return (
    <>
      <PageHeader
        title="Live agent monitor"
        subtitle="The compiled LangGraph workflow, coloured only by events this thread actually emitted. Nodes that never ran stay idle (or skipped once the run ends)."
        actions={
          <label className="flex items-center gap-2 text-sm text-fg-muted">
            <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} className="accent-[#3ecf7a]" />
            Follow new runs
          </label>
        }
      />
      {graph.error && <ErrorBox error={graph.error} onRetry={graph.reload} />}
      <div className="grid gap-4 xl:grid-cols-[minmax(0,620px)_1fr]">
        <Card
          title={thread ?? "No run selected"}
          right={<span className={`text-xs ${stream === "live" ? "text-accent" : "text-status-warning"}`}>● {stream}</span>}
        >
          <div className="mb-3"><Legend /></div>
          {graph.data ? (
            <GraphView graph={graph.data} events={events} selected={node} onSelect={setNode} finished={finished} />
          ) : (
            <Loading rows={8} />
          )}
        </Card>

        <div className="space-y-4">
          <Card title="Runs">
            {runs.error && <ErrorBox error={runs.error} />}
            {!runs.data ? <Loading /> : !runs.data.length ? (
              <Empty title="No runs yet">Run <code className="mono">make run INBOX=…</code> to start one.</Empty>
            ) : (
              <select className="input w-full" value={thread ?? ""} onChange={(e) => { setFollow(false); setThread(e.target.value); setNode(null); }}>
                {runs.data.map((r) => (
                  <option key={r.thread_id} value={r.thread_id}>
                    {r.ticket_id} · {r.tenant_id} · {fmt.label(r.status)} · {fmt.time(r.updated_at)}
                  </option>
                ))}
              </select>
            )}
            {wf && (
              <div className="mt-3 flex flex-wrap items-center gap-3 text-sm">
                <StatusBadge status={waiting ? "waiting_for_approval" : wf.status} />
                {wf.action && <span className="text-fg-muted">action: <span className="text-fg">{fmt.label(wf.action)}</span></span>}
                {wf.latency_ms != null && <span className="text-fg-muted">decision in {fmt.ms(wf.latency_ms)}</span>}
                <Link className="text-accent hover:underline" to={`/inspector?thread=${encodeURIComponent(wf.thread_id)}`}>Inspect →</Link>
              </div>
            )}
          </Card>

          <Card title={node ? `Node · ${node}` : "Execution timeline"}>
            {node ? (
              <div className="space-y-3">
                <div className="flex items-center gap-3 text-sm">
                  <StatusBadge status={state[node] ?? (finished ? "skipped" : "idle")} />
                  <button className="text-xs text-fg-faint hover:text-fg" onClick={() => setNode(null)}>back to timeline</button>
                </div>
                {!nodeEvents.length && <p className="text-sm text-fg-faint">This node did not execute in this run.</p>}
                {nodeEvents.map((e) => (
                  <div key={e.event_id} className="text-sm">
                    <div className="flex gap-3 text-fg-muted">
                      <span className="mono">{fmt.time(e.at)}</span>
                      <span>{fmt.label(e.event_type)}</span>
                      {e.duration_ms != null && <span className="mono">{fmt.ms(e.duration_ms)}</span>}
                    </div>
                    {dev && Object.keys(e.metadata).length > 0 && <div className="mt-1"><Json value={e.metadata} /></div>}
                  </div>
                ))}
                {!dev && nodeEvents.length > 0 && <p className="text-xs text-fg-faint">Turn on Developer mode to see the node's typed output summary.</p>}
              </div>
            ) : !events.length ? (
              <p className="text-sm text-fg-faint">Waiting for events…</p>
            ) : (
              <ol className="max-h-[560px] space-y-0.5 overflow-auto">
                {events.map((e) => (
                  <li key={e.event_id} className="grid grid-cols-[4.5rem_1fr_auto] items-center gap-3 rounded px-1 py-1 text-sm hover:bg-ink-850">
                    <span className="mono text-fg-faint">{fmt.time(e.at)}</span>
                    <button className="truncate text-left" onClick={() => e.node && setNode(e.node)}>
                      <span className="mono text-fg">{e.node}</span>{" "}
                      <span className="text-fg-faint">{fmt.label(e.event_type)}</span>
                      {dev && typeof e.metadata.reason_code === "string" && (
                        <span className="ml-2 text-fg-muted">route: {String(e.metadata.action)} ({e.metadata.reason_code})</span>
                      )}
                    </button>
                    <span className="flex items-center gap-2">
                      {e.duration_ms != null && <span className="mono text-fg-faint">{fmt.ms(e.duration_ms)}</span>}
                      <StatusBadge status={e.status} />
                    </span>
                  </li>
                ))}
              </ol>
            )}
          </Card>
        </div>
      </div>
    </>
  );
}
