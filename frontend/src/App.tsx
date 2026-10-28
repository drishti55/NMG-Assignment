import { Link, NavLink, Route, Routes } from "react-router-dom";
import { Empty } from "./components/ui";
import { useApi } from "./lib/api";
import { useMode, type Mode } from "./lib/mode";
import Approvals from "./pages/Approvals";
import Dashboard from "./pages/Dashboard";
import Escalations from "./pages/Escalations";
import Evaluation from "./pages/Evaluation";
import Health from "./pages/Health";
import Monitor from "./pages/Monitor";
import Runs from "./pages/Runs";
import TicketDetail from "./pages/TicketDetail";
import Tickets from "./pages/Tickets";

const NAV: { group: string; items: { to: string; label: string; badge?: "approvals" | "escalations" }[] }[] = [
  { group: "Overview", items: [{ to: "/", label: "Dashboard" }, { to: "/tickets", label: "Tickets" }] },
  {
    group: "Work queue",
    items: [
      { to: "/approvals", label: "Pending approvals", badge: "approvals" },
      { to: "/escalations", label: "Escalations", badge: "escalations" },
      { to: "/runs", label: "Run history" },
    ],
  },
  {
    group: "System",
    items: [{ to: "/monitor", label: "Agent monitor" }, { to: "/evaluation", label: "Evaluation" }, { to: "/health", label: "System health" }],
  },
];

function ModeSwitch() {
  const { mode, setMode } = useMode();
  const opt = (m: Mode, title: string, sub: string) => (
    <button
      role="radio"
      aria-checked={mode === m}
      onClick={() => setMode(m)}
      className={`rounded-md px-3 py-1.5 text-left transition-colors ${
        mode === m
          ? m === "developer" ? "bg-series-1/25 text-[#a9cdf7] ring-1 ring-series-1/60" : "bg-accent-soft text-accent ring-1 ring-accent/50"
          : "text-fg-muted hover:text-fg"
      }`}
    >
      <span className="block text-sm font-semibold leading-tight">{title}</span>
      <span className="block text-[11px] leading-tight opacity-80">{sub}</span>
    </button>
  );
  return (
    <div role="radiogroup" aria-label="Display mode" className="flex gap-1 rounded-lg border border-ink-600 bg-ink-950 p-1">
      {opt("user", "User Mode", "Simple view")}
      {opt("developer", "Developer Mode", "Technical view")}
    </div>
  );
}

export function Shell() {
  const { isDev } = useMode();
  const pending = useApi<unknown[]>("/api/approvals?status=pending", 15000);
  const cases = useApi<{ status: string }[]>("/api/escalations", 30000);
  const me = useApi<{ name: string; tenants: string[] }>("/api/me");
  const badges = { approvals: pending.data?.length ?? 0, escalations: cases.data?.filter((c) => c.status === "open").length ?? 0 };

  return (
    <div className="min-h-screen">
      <header className={`sticky top-0 z-40 border-b backdrop-blur ${isDev ? "border-series-1/40 bg-[#0b1119]/95" : "border-ink-700 bg-ink-900/95"}`}>
        <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-2.5 md:px-6">
          <Link to="/" className="flex items-center gap-2.5">
            <span className="grid h-8 w-8 place-items-center rounded-md bg-accent-soft font-mono text-sm font-bold text-accent">R</span>
            <span>
              <span className="block text-[15px] font-semibold leading-tight">Relay support desk</span>
              <span className="block text-xs text-fg-faint">AI drafts replies · people approve them</span>
            </span>
          </Link>
          <ModeSwitch />
        </div>
        {isDev && (
          <div className="border-t border-series-1/30 bg-series-1/10 px-4 py-1.5 text-xs text-[#a9cdf7] md:px-6" role="status">
            <span className="mr-2 rounded bg-series-1/30 px-1.5 py-px font-mono text-[10px] font-semibold tracking-wider">DEVELOPER MODE</span>
            Showing technical execution details: workflow nodes, model calls, token counts, retrieval scores and raw records.
            This view changes nothing about how tickets are processed or who may approve them.
          </div>
        )}
      </header>

      <div className="md:grid md:grid-cols-[230px_1fr]">
        <aside className="border-b border-ink-700 bg-ink-900 md:sticky md:top-[57px] md:h-[calc(100vh-57px)] md:border-b-0 md:border-r">
          <nav className="flex gap-4 overflow-x-auto p-3 md:flex-col md:gap-5 md:p-4" aria-label="Main">
            {NAV.map((g) => (
              <div key={g.group} className="shrink-0">
                <div className="label mb-1.5 px-2">{g.group}</div>
                <ul className="flex gap-1 md:flex-col">
                  {g.items.map((i) => (
                    <li key={i.to}>
                      <NavLink
                        to={i.to}
                        end={i.to === "/"}
                        className={({ isActive }) =>
                          `flex items-center justify-between gap-3 whitespace-nowrap rounded-md px-2.5 py-1.5 text-sm ${
                            isActive ? "bg-ink-800 font-medium text-fg" : "text-fg-muted hover:bg-ink-850 hover:text-fg"
                          }`
                        }
                      >
                        {i.label}
                        {i.badge && badges[i.badge] > 0 && (
                          <span className="rounded bg-status-warning/15 px-1.5 text-xs tabular-nums text-status-warning"
                            aria-label={`${badges[i.badge]} waiting`}>{badges[i.badge]}</span>
                        )}
                      </NavLink>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </nav>
          <p className="hidden px-6 pb-4 text-xs leading-relaxed text-fg-faint md:block">
            Signed in as <span className="text-fg-muted">{me.data?.name ?? "…"}</span> for{" "}
            {me.data?.tenants.length ?? "…"} companies. Local development sign-in, not production authentication.
          </p>
        </aside>
        <main className="min-w-0 px-4 py-6 md:px-8 md:py-8">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/tickets" element={<Tickets />} />
            <Route path="/tickets/:tenant/:ticketId" element={<TicketDetail />} />
            <Route path="/approvals" element={<Approvals />} />
            <Route path="/escalations" element={<Escalations />} />
            <Route path="/runs" element={<Runs />} />
            <Route path="/monitor" element={<Monitor />} />
            <Route path="/evaluation" element={<Evaluation />} />
            <Route path="/health" element={<Health />} />
            <Route path="*" element={<Empty title="Page not found" action={<Link className="btn-ghost" to="/">Go to the dashboard</Link>}>This address does not match any page.</Empty>} />
          </Routes>
        </main>
      </div>
    </div>
  );
}
