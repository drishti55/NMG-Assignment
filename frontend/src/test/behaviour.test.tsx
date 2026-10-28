import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { baseRoutes, ticketNotProcessed, ticketRows } from "./fixtures";
import { mockApi, renderAt, writes } from "./harness";

beforeEach(() => localStorage.clear());
afterEach(cleanup);

describe("display modes", () => {
  it("starts in User Mode for a new user", async () => {
    mockApi({ ...baseRoutes, "GET /api/tickets/summary": { total: 0, by_state: {} } });
    renderAt("/");
    expect(screen.getByRole("radio", { name: /User Mode/ })).toHaveProperty("ariaChecked", "true");
    expect(screen.queryByText("DEVELOPER MODE")).toBeNull();
    await screen.findByText("No data yet");
  });

  it("switching modes changes the view but not the ticket, and starts nothing", async () => {
    const calls = mockApi({ ...baseRoutes, "GET /api/tickets/northwind/NW-T01": ticketNotProcessed });
    renderAt("/tickets/northwind/NW-T01");
    await screen.findByRole("heading", { name: "Return a helmet" });
    expect(screen.queryByText(/LangGraph node timeline/)).toBeNull();

    fireEvent.click(screen.getByRole("radio", { name: /Developer Mode/ }));
    expect(await screen.findByText("DEVELOPER MODE")).toBeTruthy();
    expect(screen.getByText(/LangGraph node timeline/)).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Return a helmet" })).toBeTruthy(); // same ticket

    fireEvent.click(screen.getByRole("radio", { name: /User Mode/ }));
    await waitFor(() => expect(screen.queryByText(/LangGraph node timeline/)).toBeNull());
    expect(screen.getByRole("heading", { name: "Return a helmet" })).toBeTruthy();
    expect(writes(calls)).toEqual([]); // no processing, no approvals, nothing written
  });
});

describe("ticket processing only happens on an explicit, confirmed request", () => {
  it("opening a ticket only reads data", async () => {
    const calls = mockApi({ ...baseRoutes, "GET /api/tickets/northwind/NW-T01": ticketNotProcessed });
    renderAt("/tickets/northwind/NW-T01");
    await screen.findByRole("button", { name: "Process this ticket" });
    expect(writes(calls)).toEqual([]);
  });

  it("names the exact ticket, can be cancelled, and sends exactly one request", async () => {
    const calls = mockApi({
      ...baseRoutes, "GET /api/tickets/northwind/NW-T01": ticketNotProcessed,
      "POST /api/tickets/northwind/NW-T01/process": { run_id: "r", outcome: "enqueued", thread_id: "northwind:NW-T01:g1" },
    });
    renderAt("/tickets/northwind/NW-T01");
    fireEvent.click(await screen.findByRole("button", { name: "Process this ticket" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("NW-T01")).toBeTruthy();
    expect(within(dialog).getByText(/Return a helmet/)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(writes(calls)).toEqual([]);

    fireEvent.click(screen.getByRole("button", { name: "Process this ticket" }));
    const confirm = within(screen.getByRole("dialog")).getByRole("button", { name: "Yes, process this ticket" });
    fireEvent.click(confirm);
    fireEvent.click(confirm); // a double click must not send twice
    await screen.findByText(/Processing started/);
    expect(writes(calls).map((c) => `${c.method} ${c.url}`)).toEqual(["POST /api/tickets/northwind/NW-T01/process"]);
  });

  it("shows only the actions the backend allows", async () => {
    mockApi({ ...baseRoutes, "GET /api/tickets/northwind/NW-T01": { ...ticketNotProcessed, view: { state: "sent", allowed_actions: [], pending_approval_id: null } } });
    renderAt("/tickets/northwind/NW-T01");
    await screen.findByText(/Nothing to do. The reply was approved and sent./);
    expect(screen.queryByRole("button", { name: /Process this ticket|Approve reply/ })).toBeNull();
  });
});

describe("ticket list", () => {
  it("keeps the backend order, selects nothing, and processes nothing", async () => {
    const calls = mockApi({
      ...baseRoutes, "GET /api/tickets": ticketRows, "GET /api/inbox": [],
      "GET /api/tickets/summary": { total: 3, by_state: { not_processed: 1, escalated: 1, waiting_approval: 1 } },
    });
    renderAt("/tickets");
    await screen.findByText("Thank you");
    const ids = screen.getAllByRole("row").slice(1).map((r) => within(r).getAllByRole("cell")[0].textContent);
    expect(ids).toEqual(["HD-T12", "NW-T12", "NW-T01"]);
    expect(screen.getByText("Waiting for your approval", { selector: "span" })).toBeTruthy();
    expect(writes(calls)).toEqual([]);
  });

  it("shows a helpful empty state instead of sample data", async () => {
    mockApi({ ...baseRoutes, "GET /api/tickets": [], "GET /api/inbox": [], "GET /api/tickets/summary": { total: 0, by_state: {} } });
    renderAt("/tickets");
    expect(await screen.findByText("No tickets yet")).toBeTruthy();
  });
});

describe("work queues never auto-select", () => {
  it("approvals page waits for an explicit choice", async () => {
    mockApi({ ...baseRoutes, "GET /api/approvals": [{ approval_id: "apr-1", tenant_id: "northwind", ticket_id: "NW-T01", kind: "draft_reply",
      attempt: 1, status: "pending", priority: "normal" }] });
    renderAt("/approvals");
    expect(await screen.findByText("Select a reply to review")).toBeTruthy();
  });

  it("agent monitor does not follow new runs unless asked", async () => {
    const calls = mockApi({ ...baseRoutes, "GET /api/runs": [{ thread_id: "northwind:NW-T01:g1", tenant_id: "northwind", ticket_id: "NW-T01",
      run_id: "r", status: "completed", current_node: null, started_at: "2026-10-28T10:00:00Z", updated_at: "2026-10-28T10:00:00Z",
      action: "draft_reply", decision_status: "pending_approval", latency_ms: 1 }], "GET /api/graph": { nodes: [], edges: [] } });
    renderAt("/monitor");
    expect(await screen.findByText("Select a run to watch")).toBeTruthy();
    expect(calls.some((c) => c.method === "SSE" && c.url.includes("/api/stream"))).toBe(false);
    expect((screen.getByRole("checkbox") as HTMLInputElement).checked).toBe(false);
  });
});
