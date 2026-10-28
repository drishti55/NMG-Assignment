import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Card, ConfirmDialog, Empty, ErrorBox, Loading, Notice, PageHeader, StateBadge } from "../components/ui";
import { explainError, fmt, post, useApi } from "../lib/api";
import { useMode } from "../lib/mode";
import { PRIORITY, STATE, TENANT, sourceLabel } from "../lib/status";

export interface TicketRow {
  tenant_id: string; ticket_id: string; customer_ref: string; subject: string; received_at: string; source: string | null;
  category: string | null; priority: string | null; action: string | null; decision_status: string | null;
  injection_detected: boolean | null; latency_ms: number | null; tokens_in: number | null; tokens_out: number | null;
  reason_code: string | null; state: string; allowed_actions: string[];
}
interface Summary { total: number; by_state: Record<string, number> }

const STATE_ORDER = ["waiting_approval", "escalated", "not_processed", "processing", "sent", "held_budget", "failed"];

export default function Tickets() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const { isDev } = useMode();
  const state = params.get("state") ?? "";
  const tenant = params.get("tenant") ?? "";
  const q = params.get("q") ?? "";
  const [search, setSearch] = useState(q);
  const qs = new URLSearchParams(Object.entries({ state, tenant, q }).filter(([, v]) => v)).toString();
  const rows = useApi<TicketRow[]>(`/api/tickets${qs ? `?${qs}` : ""}`, 10000);
  const summary = useApi<Summary>(`/api/tickets/summary${tenant ? `?tenant=${tenant}` : ""}`, 10000);
  const tenants = useApi<{ tenant_id: string; name: string }[]>("/api/tenants");

  const setFilter = (k: string, v: string) => {
    const next = new URLSearchParams(params);
    if (v) next.set(k, v); else next.delete(k);
    setParams(next, { replace: true });
  };

  return (
    <>
      <PageHeader
        title="Tickets"
        purpose="Every customer ticket the system has received, newest first. Each one shows where it stands: not processed yet, waiting for your approval, handed to a person, or answered."
        next="Click a ticket to open it. Processing only starts when you choose “Process this ticket” on its page."
      />

      <ImportPanel onImported={() => { rows.reload(); summary.reload(); }} empty={summary.data?.total === 0} />

      <div className="mb-3 flex flex-wrap gap-2" role="group" aria-label="Filter by status">
        <Chip active={!state} onClick={() => setFilter("state", "")} label="All" count={summary.data?.total} />
        {STATE_ORDER.map((s) => (
          <Chip key={s} active={state === s} onClick={() => setFilter("state", s)} label={STATE[s].label} count={summary.data?.by_state[s]} />
        ))}
      </div>
      <form className="mb-4 flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); setFilter("q", search.trim()); }}>
        <label className="sr-only" htmlFor="ticket-search">Search tickets</label>
        <input id="ticket-search" className="input w-72" placeholder="Search by ticket ID, subject or message" value={search} onChange={(e) => setSearch(e.target.value)} />
        <button className="btn-ghost" type="submit">Search</button>
        <label className="sr-only" htmlFor="tenant-filter">Company</label>
        <select id="tenant-filter" className="input" value={tenant} onChange={(e) => setFilter("tenant", e.target.value)}>
          <option value="">All companies</option>
          {tenants.data?.map((t) => <option key={t.tenant_id} value={t.tenant_id}>{t.name}</option>)}
        </select>
        {(state || tenant || q) && <button type="button" className="btn-ghost" onClick={() => { setSearch(""); setParams({}, { replace: true }); }}>Clear filters</button>}
      </form>

      {rows.error && <ErrorBox error={rows.error} onRetry={rows.reload} />}
      <Card>
        {rows.loading && !rows.data ? <Loading label="Loading tickets" /> : !rows.data?.length ? (
          summary.data?.total === 0 ? (
            <Empty title="No tickets yet">Import an inbox above, or run <code className="mono">make demo</code>, to load the assessment's tickets. Importing does not process them.</Empty>
          ) : <Empty title="No tickets match these filters" action={<button className="btn-ghost" onClick={() => setParams({}, { replace: true })}>Show all tickets</button>} />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <caption className="sr-only">Tickets, newest first</caption>
              <thead className="text-left text-fg-faint">
                <tr>
                  <th className="pb-2 pr-4 font-medium">Ticket</th>
                  <th className="pb-2 pr-4 font-medium">Subject</th>
                  <th className="pb-2 pr-4 font-medium">Company</th>
                  <th className="pb-2 pr-4 font-medium">Priority</th>
                  <th className="pb-2 pr-4 font-medium">Status</th>
                  <th className="pb-2 font-medium">Received</th>
                  {isDev && <th className="pb-2 pl-4 font-mono text-xs font-medium text-[#8fbcf2]">category / reason / tokens</th>}
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-800">
                {rows.data.map((r) => {
                  const href = `/tickets/${r.tenant_id}/${encodeURIComponent(r.ticket_id)}`;
                  return (
                    <tr key={`${r.tenant_id}/${r.ticket_id}`} className="cursor-pointer hover:bg-ink-850" onClick={() => navigate(href)}>
                      <td className="py-2.5 pr-4"><Link to={href} className="font-mono hover:text-accent" onClick={(e) => e.stopPropagation()}>{r.ticket_id}</Link></td>
                      <td className="max-w-[22rem] pr-4">
                        <span className="block truncate">{r.subject}</span>
                        {r.injection_detected && <span className="text-xs text-[#ef6b6b]">▲ Possible manipulation attempt</span>}
                      </td>
                      <td className="whitespace-nowrap pr-4 text-fg-muted">{TENANT[r.tenant_id] ?? r.tenant_id}</td>
                      <td className="pr-4 text-fg-muted">{r.priority ? PRIORITY[r.priority]?.label : "—"}</td>
                      <td className="pr-4"><StateBadge state={r.state} /></td>
                      <td className="whitespace-nowrap text-fg-faint" title={sourceLabel(r.source)}>{fmt.datetime(r.received_at)}</td>
                      {isDev && (
                        <td className="whitespace-nowrap pl-4 font-mono text-xs text-fg-muted">
                          {r.category ?? "-"} / {r.reason_code ?? "-"} / {r.tokens_in ?? "-"}→{r.tokens_out ?? "-"}
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}

function Chip({ label, count, active, onClick }: { label: string; count?: number; active: boolean; onClick: () => void }) {
  return (
    <button type="button" aria-pressed={active} onClick={onClick}
      className={`rounded-full border px-3 py-1 text-sm ${active ? "border-accent bg-accent-soft text-accent" : "border-ink-600 text-fg-muted hover:text-fg"}`}>
      {label} <span className="tabular-nums opacity-80">{count ?? "…"}</span>
    </button>
  );
}

interface InboxFile { file: string; tickets: number; already_imported: number; rejected_lines: number }

/** Loads tickets from the assessment's inbox files without processing them. */
function ImportPanel({ onImported, empty }: { onImported: () => void; empty: boolean }) {
  const files = useApi<InboxFile[]>("/api/inbox");
  const [open, setOpen] = useState<InboxFile | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: "good" | "critical"; text: string } | null>(null);
  const importable = files.data?.filter((f) => f.already_imported < f.tickets) ?? [];
  if (!empty && importable.length === 0 && !msg) return null;

  async function doImport(f: InboxFile) {
    setBusy(true);
    try {
      const r = await post<{ imported: string[]; already_present: string[]; rejected: unknown[] }>("/api/inbox/import", { file: f.file });
      setMsg({ tone: "good", text: `Imported ${r.imported.length} new ticket(s)${r.already_present.length ? `; ${r.already_present.length} were already here` : ""}. They are marked “Not processed yet”.` });
      files.reload();
      onImported();
    } catch (e) {
      setMsg({ tone: "critical", text: explainError(e) });
    } finally {
      setBusy(false);
      setOpen(null);
    }
  }

  return (
    <Card title="Load tickets from an inbox file" className="mb-5"
      help="Importing only stores the tickets so you can see them. It does not run the AI on them.">
      {msg && <div className="mb-3"><Notice tone={msg.tone}>{msg.text}</Notice></div>}
      {files.error ? <ErrorBox error={files.error} /> : !files.data ? <Loading /> : (
        <div className="flex flex-wrap gap-2">
          {files.data.map((f) => (
            <button key={f.file} className="btn-ghost" disabled={f.already_imported >= f.tickets} onClick={() => setOpen(f)}>
              Import {f.file} ({f.already_imported >= f.tickets ? "already imported" : `${f.tickets - f.already_imported} new`})
            </button>
          ))}
        </div>
      )}
      <ConfirmDialog open={!!open} title="Import these tickets?" confirmLabel="Import tickets" busy={busy}
        onConfirm={() => open && doImport(open)} onCancel={() => setOpen(null)}>
        <p>This adds the tickets in <span className="font-mono text-fg">{open?.file}</span> to the list as “Not processed yet”.</p>
        <p>No AI processing happens. You choose which tickets to process afterwards. Tickets already present are left unchanged.</p>
      </ConfirmDialog>
    </Card>
  );
}
