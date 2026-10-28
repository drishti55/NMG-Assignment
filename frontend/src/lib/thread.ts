import { useEffect, useState } from "react";
import { useEventStream, type RelayEvent, type StreamState } from "./api";

/** All events of one LangGraph thread: replayed from the start, then live. */
export function useThreadEvents(threadId: string | null): { events: RelayEvent[]; stream: StreamState } {
  const [events, setEvents] = useState<RelayEvent[]>([]);
  useEffect(() => setEvents([]), [threadId]);
  const stream = useEventStream(threadId ? `/api/runs/${encodeURIComponent(threadId)}/stream?after=0` : null, (e) =>
    setEvents((prev) => (prev.some((p) => p.event_id === e.event_id) ? prev : [...prev, e])),
  );
  return { events, stream };
}

export interface WorkflowRow {
  thread_id: string;
  tenant_id: string;
  ticket_id: string;
  run_id: string;
  status: string;
  current_node: string | null;
  started_at: string;
  updated_at: string;
  action: string | null;
  decision_status: string | null;
  latency_ms: number | null;
}
