import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { Shell } from "../App";
import { ModeProvider } from "../lib/mode";

/** Records every HTTP request the UI makes and answers from a route table. */
export function mockApi(routes: Record<string, unknown>) {
  const calls: { method: string; url: string; body?: string }[] = [];
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ method, url, body: init?.body as string | undefined });
    const key = `${method} ${url.split("?")[0]}`;
    const hit = Object.entries(routes).find(([k]) => k === key || (k.endsWith("*") && key.startsWith(k.slice(0, -1))));
    if (!hit) return new Response(JSON.stringify({ detail: `no mock for ${key}` }), { status: 404 });
    const value = typeof hit[1] === "function" ? (hit[1] as () => unknown)() : hit[1];
    return new Response(JSON.stringify(value), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("EventSource", class {
    onopen: (() => void) | null = null;
    onerror: (() => void) | null = null;
    constructor(public url: string) { calls.push({ method: "SSE", url }); }
    addEventListener() {}
    close() {}
  });
  return calls;
}

export function renderAt(path: string) {
  return render(
    <ModeProvider>
      <MemoryRouter initialEntries={[path]}>
        <Shell />
      </MemoryRouter>
    </ModeProvider>,
  );
}

export const writes = <T extends { method: string }>(calls: T[]) => calls.filter((c) => c.method !== "GET" && c.method !== "SSE");
