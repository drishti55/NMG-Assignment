import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, NavLink, Route, Routes } from "react-router-dom";
import "./index.css";
import { useApi } from "./lib/api";
import { DevModeProvider, useDevMode } from "./lib/devmode";
import Dashboard from "./pages/Dashboard";
import Monitor from "./pages/Monitor";
import Inspector from "./pages/Inspector";
import Tickets from "./pages/Tickets";
import Approvals from "./pages/Approvals";
import Escalations from "./pages/Escalations";
import Evaluation from "./pages/Evaluation";
import Health from "./pages/Health";

const NAV = [
  ["/", "Operations"],
  ["/monitor", "Live monitor"],
  ["/inspector", "Run inspector"],
  ["/tickets", "Tickets"],
  ["/approvals", "Approvals"],
  ["/escalations", "Escalations"],
  ["/evaluation", "Evaluation"],
  ["/health", "System health"],
] as const;

function DevToggle() {
  const { on, toggle } = useDevMode();
  return (
    <button
      role="switch"
      aria-checked={on}
      onClick={toggle}
      className="flex w-full items-center justify-between rounded-md border border-ink-700 px-3 py-2 text-sm hover:border-ink-600"
    >
      <span className={on ? "text-accent" : "text-fg-muted"}>Developer mode</span>
      <span className={`relative h-5 w-9 rounded-full transition-colors ${on ? "bg-accent-strong" : "bg-ink-600"}`}>
        <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-fg transition-all ${on ? "left-[18px]" : "left-0.5"}`} />
      </span>
    </button>
  );
}

function Shell() {
  const me = useApi<{ name: string; tenants: string[] }>("/api/me");
  const pending = useApi<unknown[]>("/api/approvals?status=pending", 10000);
  return (
    <div className="min-h-screen md:grid md:grid-cols-[220px_1fr]">
      <aside className="border-b border-ink-700 bg-ink-900 md:sticky md:top-0 md:h-screen md:border-b-0 md:border-r">
        <div className="flex h-full flex-col gap-4 p-4">
          <div className="flex items-center gap-2">
            <span className="grid h-7 w-7 place-items-center rounded-md bg-accent-soft font-mono text-sm font-bold text-accent">R</span>
            <div>
              <div className="text-sm font-semibold leading-tight">Relay</div>
              <div className="text-[11px] text-fg-faint">support-desk agents</div>
            </div>
          </div>
          <nav className="flex gap-1 overflow-x-auto md:flex-col">
            {NAV.map(([to, label]) => (
              <NavLink
                key={to}
                to={to}
                end={to === "/"}
                className={({ isActive }) =>
                  `flex items-center justify-between whitespace-nowrap rounded-md px-3 py-1.5 text-sm ${
                    isActive ? "bg-ink-800 text-fg" : "text-fg-muted hover:bg-ink-850 hover:text-fg"
                  }`
                }
              >
                {label}
                {to === "/approvals" && !!pending.data?.length && (
                  <span className="ml-2 rounded bg-status-warning/15 px-1.5 text-[11px] tabular-nums text-status-warning">
                    {pending.data.length}
                  </span>
                )}
              </NavLink>
            ))}
          </nav>
          <div className="mt-auto hidden space-y-3 md:block">
            <DevToggle />
            <div className="text-[11px] leading-relaxed text-fg-faint">
              Signed in as <span className="text-fg-muted">{me.data?.name ?? "…"}</span>
              <br />
              Tenants: {me.data?.tenants.join(", ") ?? "…"}
              <br />
              Local dev identity, not production auth.
            </div>
          </div>
        </div>
      </aside>
      <main className="min-w-0 px-4 py-6 md:px-8">
        <div className="mb-4 md:hidden"><DevToggle /></div>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/monitor" element={<Monitor />} />
          <Route path="/inspector" element={<Inspector />} />
          <Route path="/tickets" element={<Tickets />} />
          <Route path="/approvals" element={<Approvals />} />
          <Route path="/escalations" element={<Escalations />} />
          <Route path="/evaluation" element={<Evaluation />} />
          <Route path="/health" element={<Health />} />
        </Routes>
      </main>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <DevModeProvider>
      <BrowserRouter>
        <Shell />
      </BrowserRouter>
    </DevModeProvider>
  </React.StrictMode>,
);
