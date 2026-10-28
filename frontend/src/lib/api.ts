import { useCallback, useEffect, useRef, useState } from "react";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export interface Loaded<T> {
  data: T | undefined;
  error: string | undefined;
  loading: boolean;
  reload: () => void;
}

/** Fetch `path`, optionally re-fetching every `intervalMs`. `path = null` skips. */
export function useApi<T>(path: string | null, intervalMs?: number): Loaded<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<string>();
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    if (!path) return;
    let alive = true;
    setLoading(true);
    api<T>(path)
      .then((d) => alive && (setData(d), setError(undefined)))
      .catch((e: Error) => alive && setError(e.message))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [path, tick]);

  useEffect(() => {
    if (!intervalMs) return;
    const id = setInterval(reload, intervalMs);
    return () => clearInterval(id);
  }, [intervalMs, reload]);

  return { data, error, loading, reload };
}

export interface RelayEvent {
  event_id: number;
  tenant_id: string;
  run_id: string | null;
  thread_id: string | null;
  ticket_id: string | null;
  event_type: string;
  node: string | null;
  status: string | null;
  duration_ms: number | null;
  metadata: Record<string, unknown>;
  at: string;
}

export type StreamState = "connecting" | "live" | "reconnecting";

/** Server-Sent Events from Postgres-backed events. On a dropped connection EventSource
 * reconnects and sends Last-Event-ID, so the server replays what was missed. */
export function useEventStream(path: string | null, onEvent: (e: RelayEvent) => void): StreamState {
  const [state, setState] = useState<StreamState>("connecting");
  const handler = useRef(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    if (!path) return;
    const es = new EventSource(path);
    es.onopen = () => setState("live");
    es.onerror = () => setState("reconnecting");
    es.addEventListener("relay", (msg) => handler.current(JSON.parse((msg as MessageEvent).data)));
    return () => es.close();
  }, [path]);

  return state;
}

export const fmt = {
  int: (n: number | null | undefined) => (n == null ? "—" : n.toLocaleString("en-US")),
  ms: (n: number | null | undefined) =>
    n == null ? "—" : n >= 1000 ? `${(n / 1000).toFixed(1)}s` : `${Math.round(n)}ms`,
  pct: (n: number | null | undefined) => (n == null ? "—" : `${(n * 100).toFixed(1)}%`),
  time: (s: string | null | undefined) =>
    s ? new Date(s).toLocaleTimeString("en-GB", { hour12: false }) : "—",
  datetime: (s: string | null | undefined) =>
    s ? new Date(s).toLocaleString("en-GB", { hour12: false, dateStyle: "short", timeStyle: "medium" }) : "—",
  label: (s: string | null | undefined) => (s ? s.replace(/_/g, " ") : "—"),
};
