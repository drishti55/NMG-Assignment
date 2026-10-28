/// <reference types="vite/client" />
import { describe, expect, it } from "vitest";
import { deriveProgress } from "../lib/progress";
import { REASON, STATE, reasonText } from "../lib/status";
import { draftWaiting, escalatedEarly } from "./fixtures";

describe("plain-language status catalog", () => {
  it("describes every state the backend can return (relay/ticket_view.py STATES)", () => {
    for (const s of ["not_processed", "processing", "waiting_approval", "sent", "escalated", "held_budget", "failed"]) {
      expect(STATE[s]?.label).toBeTruthy();
      expect(STATE[s].label).not.toContain("_");
      expect(STATE[s].help.length).toBeGreaterThan(20);
    }
  });
  it("explains every reason code the lead agent records", () => {
    for (const code of ["injection", "clinical", "legal_threat", "money_dispute", "kb_not_covered", "checker_failed",
      "rejected_twice", "model_proposed", "dead_letter", "consent_required"]) expect(REASON[code]).toBeTruthy();
    expect(reasonText("invalid_model_output:triage")).toMatch(/unusable output/);
    expect(reasonText("something_new")).not.toContain("something_new");
  });
});

describe("progress tracker derives stages only from recorded events", () => {
  it("shows nothing as done before processing starts", () => {
    const p = deriveProgress([]);
    expect(p.started).toBe(false);
    expect(p.stages.every((s) => s.status === "todo")).toBe(true);
  });
  it("marks a drafted ticket as waiting for approval, not sent", () => {
    const p = deriveProgress(draftWaiting);
    const by = Object.fromEntries(p.stages.map((s) => [s.key, s.status]));
    expect(by).toMatchObject({ received: "done", understood: "done", knowledge: "done", drafted: "done", checked: "done", approval: "waiting", sent: "todo" });
    expect(p.outcome).toBe("waiting_approval");
  });
  it("marks steps an escalation skipped as skipped, with the reason", () => {
    const p = deriveProgress(escalatedEarly);
    const stage = (k: string) => p.stages.find((s) => s.key === k)!;
    expect(stage("understood").status).toBe("done");
    for (const k of ["knowledge", "drafted", "checked", "approval", "sent"]) {
      expect(stage(k).status).toBe("skipped");
      expect(stage(k).note).toMatch(/handed to a person/);
    }
    expect(p.outcome).toBe("escalated");
  });
  it("is unaffected by duplicate events", () => {
    expect(deriveProgress([...draftWaiting, ...draftWaiting]).stages).toEqual(deriveProgress(draftWaiting).stages);
  });
});

describe("no randomness in the console", () => {
  // Every application source file (tests excluded), loaded as text by Vite.
  const sources = import.meta.glob(["../**/*.{ts,tsx}", "!../test/**"], { query: "?raw", import: "default", eager: true }) as Record<string, string>;
  it("found the application sources", () => expect(Object.keys(sources).length).toBeGreaterThan(10));
  it.each(Object.entries(sources))("%s uses no random selection or shuffling", (_file, src) => {
    expect(src).not.toMatch(/Math\.random|shuffle|sort\(\s*\(\)\s*=>/);
  });
});
