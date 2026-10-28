import { Link } from "react-router-dom";
import { fmt, type RelayEvent } from "../lib/api";
import { StatusBadge } from "./ui";

export function EventFeed({ events, limit = 30 }: { events: RelayEvent[]; limit?: number }) {
  if (!events.length)
    return <p className="text-sm text-fg-faint">No execution events yet. Run an inbox to see the graph execute.</p>;
  return (
    <ol className="divide-y divide-ink-800">
      {events.slice(0, limit).map((e) => (
        <li key={e.event_id} className="grid grid-cols-[4.5rem_1fr_auto] items-center gap-3 py-1.5 text-sm">
          <span className="mono text-fg-faint">{fmt.time(e.at)}</span>
          <span className="min-w-0 truncate">
            <Link to={`/inspector?thread=${encodeURIComponent(e.thread_id ?? "")}`} className="text-fg hover:text-accent">
              {e.ticket_id}
            </Link>
            <span className="text-fg-faint"> · {e.tenant_id} · </span>
            <span className="mono text-fg-muted">{e.node ?? "-"}</span>
            <span className="text-fg-faint"> {fmt.label(e.event_type)}</span>
          </span>
          <span className="flex items-center gap-2">
            {e.duration_ms != null && <span className="mono text-fg-faint">{fmt.ms(e.duration_ms)}</span>}
            <StatusBadge status={e.status} />
          </span>
        </li>
      ))}
    </ol>
  );
}
