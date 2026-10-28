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

export const post = <T,>(path: string, body?: unknown) =>
  api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

/** Turns an API failure into what happened + what to do next. */
export function explainError(e: unknown): string {
  if (!(e instanceof ApiError)) return "The console could not reach the Relay API. Check that it is running (make dev) and try again.";
  if (e.status === 401) return "You are not signed in to the API. Restart the console with `make dev`.";
  if (e.status === 403) return "Your account is not allowed to see or change this company's data.";
  if (e.status === 404) return "This item no longer exists. Go back to the list and choose another one.";
  if (e.status === 409) return `${capitalize(e.message)}. Refresh to see its current state.`;
  if (e.status === 422) return "Some required information is missing or invalid. Check the form and try again.";
  return `The server reported an error (${e.status}): ${e.message}. Try again; if it persists, check System health.`;
}

const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

export interface Loaded<T> {
  data: T | undefined;
  error: unknown;
  loading: boolean;
  reload: () => void;
}

/** GET `path`, optionally every `intervalMs`. Switching `path` aborts the previous request,
 * so a slow response for another item can never overwrite the current one. `null` skips. */
export function useApi<T>(path: string | null, intervalMs?: number): Loaded<T> {
  const [state, setState] = useState<{ path: string | null; data?: T; error?: unknown }>({ path });
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    if (!path) return;
    const ctrl = new AbortController();
    api<T>(path, { signal: ctrl.signal })
      .then((data) => setState({ path, data }))
      .catch((error) => {
        if (!ctrl.signal.aborted) setState((s) => ({ ...s, path, error }));
      });
    return () => ctrl.abort();
  }, [path, tick]);

  useEffect(() => {
    if (!intervalMs || !path) return;
    const id = setInterval(reload, intervalMs);
    return () => clearInterval(id);
  }, [intervalMs, path, reload]);

  const current = state.path === path; // never show data that belongs to a previous path
  return {
    data: current ? state.data : undefined,
    error: current ? state.error : undefined,
    loading: !current || (state.data === undefined && state.error === undefined),
    reload,
  };
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

/** Server-Sent Events backed by Postgres. After a dropped connection EventSource
 * reconnects with Last-Event-ID and the server replays what was missed. */
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
    n == null ? "—" : n >= 1000 ? `${(n / 1000).toFixed(1)} s` : `${Math.round(n)} ms`,
  pct: (n: number | null | undefined) => (n == null ? "—" : `${(n * 100).toFixed(1)}%`),
  time: (s: string | null | undefined) =>
    s ? new Date(s).toLocaleTimeString("en-GB", { hour12: false }) : "—",
  datetime: (s: string | null | undefined) =>
    s ? new Date(s).toLocaleString("en-GB", { hour12: false, dateStyle: "medium", timeStyle: "short" }) : "—",
  label: (s: string | null | undefined) => (s ? s.replace(/_/g, " ") : "—"),
  words: (s: string | null | undefined) => (s ? s.trim().split(/\s+/).length : 0),
};
