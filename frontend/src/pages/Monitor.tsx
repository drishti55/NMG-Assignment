import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { NodeTimeline, ProgressTracker } from "../components/Execution";
import { GraphView, Legend, type GraphShape } from "../components/GraphView";
import { Card, DevPanel, Empty, ErrorBox, Loading, PageHeader } from "../components/ui";
import { fmt, useApi, useEventStream } from "../lib/api";
import { useMode } from "../lib/mode";
import { NODE, TENANT } from "../lib/status";
import { useThreadEvents, type WorkflowRow } from "../lib/thread";

export default function Monitor() {
  const [params, setParams] = useSearchParams();
  const thread = params.get("thread");
  const runs = useApi<WorkflowRow[]>("/api/runs?limit=100", 10000);
  const graph = useApi<GraphShape>("/api/graph");
  const { isDev } = useMode();
  // Opt-in only: when on, the view switches to each newly started run. Off by default so the
  // screen never changes ticket on its own.
  const [follow, setFollow] = useState(false);
  useEventStream(follow ? "/api/stream?after=-1" : null, (e) => {
    if (e.thread_id && e.event_type === "run_started") setParams({ thread: e.thread_id }, { replace: true });
  });
  const { events, stream } = useThreadEvents(thread);
  const finished = events.some((e) => e.event_type === "run_finished");
  const [node, setNode] = useState<string | null>(null);
  const wf = runs.data?.find((r) => r.thread_id === thread);

  return (
    <>
      <PageHeader
        title="Agent monitor"
        purpose={isDev
          ? "The real compiled LangGraph workflow. A node lights up only when this run recorded that it executed; branches that did not run stay grey."
          : "Watch one ticket move through the AI's steps as it happens. Choose a run below, or turn on “Follow new runs” before processing a ticket."}
        actions={
          <label className="flex max-w-xs items-start gap-2 text-sm">
            <input type="checkbox" className="mt-1 accent-[#3ecf7a]" checked={follow} onChange={(e) => setFollow(e.target.checked)} />
            <span><span className="font-medium">Follow new runs</span><span className="block text-xs text-fg-faint">When on, this page switches to each ticket as soon as it starts processing.</span></span>
          </label>
        }
      />
      {runs.error && <ErrorBox error={runs.error} onRetry={runs.reload} />}
      <Card className="mb-5">
        <label className="label mb-1.5 block" htmlFor="run-pick">Run to show</label>
        {!runs.data ? <Loading /> : !runs.data.length ? <p className="text-sm text-fg-muted">No runs yet. Process a ticket from its page first.</p> : (
          <select id="run-pick" className="input w-full max-w-2xl" value={thread ?? ""} onChange={(e) => { setNode(null); setParams(e.target.value ? { thread: e.target.value } : {}); }}>
            <option value="">Select a run…</option>
            {runs.data.map((r) => (
              <option key={r.thread_id} value={r.thread_id}>
                {r.ticket_id} · {TENANT[r.tenant_id] ?? r.tenant_id} · started {fmt.datetime(r.started_at)}{isDev ? ` · ${r.thread_id}` : ""}
              </option>
            ))}
          </select>
        )}
        {wf && <Link className="ml-3 text-sm text-accent hover:underline" to={`/tickets/${wf.tenant_id}/${encodeURIComponent(wf.ticket_id)}`}>Open the ticket →</Link>}
      </Card>

      {!thread ? <Empty title="Select a run to watch">Nothing is selected automatically, so the screen never jumps to another ticket by itself.</Empty> : isDev ? (
        <div className="grid gap-5 xl:grid-cols-[minmax(0,620px)_1fr]">
          <DevPanel title={`Compiled graph · ${thread} · SSE ${stream}`} explain="Nodes and edges come from GET /api/graph (the compiled StateGraph). Colour = this thread's recorded events.">
            <div className="mb-3"><Legend /></div>
            {graph.data ? <GraphView graph={graph.data} events={events} selected={node} onSelect={setNode} finished={finished} /> : <Loading />}
          </DevPanel>
          <DevPanel title={node ? `Node: ${node}` : "Node timeline"} explain={node ? NODE[node] ?? "" : "Click a node in the graph to filter."}>
            {node && <button className="mb-2 text-xs text-fg-faint hover:text-fg" onClick={() => setNode(null)}>Show all nodes</button>}
            <NodeTimeline events={node ? events.filter((e) => e.node === node) : events} />
          </DevPanel>
        </div>
      ) : (
        <Card title="Progress" help="Each step is marked only when the system recorded that it happened.">
          <ProgressTracker events={events} />
          <p className="mt-4 text-xs text-fg-faint">Switch to Developer Mode at the top to see the full workflow graph with every branch.</p>
        </Card>
      )}
    </>
  );
}
