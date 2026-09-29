import { describe, expect, it } from "vitest";

import { buildCsv } from "@/components/list/exportCsv";
import { parseListState } from "@/components/list/list-state";

import { AUDIT_LIST, auditVariables, sinceToIso } from "./audit-list";
import {
  fromRunAuditItem,
  outcomeOf,
  RUN_AUDIT_LIST,
  RUN_CSV,
  type RunAuditItem,
  runAuditVariables,
} from "./combined-runs";
import { COMBINED_RUNS } from "./fixtures";

const NOW = Date.UTC(2026, 8, 27, 14, 0, 0);

describe("sinceToIso", () => {
  it("reads relative windows and dates, and nothing else", () => {
    expect(sinceToIso("24h", NOW)).toBe("2026-09-26T14:00:00.000Z");
    expect(sinceToIso("7d", NOW)).toBe("2026-09-20T14:00:00.000Z");
    expect(sinceToIso("2026-09-01", NOW)).toBe("2026-09-01T00:00:00.000Z");
    expect(sinceToIso("yesterday", NOW)).toBeNull();
    expect(sinceToIso(undefined, NOW)).toBeNull();
  });
});

describe("auditVariables", () => {
  const opts = { pageSize: 100, after: null, now: NOW };

  it("sends the default state as the route's preload does", () => {
    const state = parseListState(AUDIT_LIST, "");
    expect(auditVariables(state.filters, state.q, opts)).toEqual({
      limit: 100,
      after: null,
      search: null,
      filter: null,
      includeTotal: true,
    });
  });

  it("sends Mine as actor me, for the server to resolve", () => {
    const mine = AUDIT_LIST.views.find((v) => v.key === "mine");
    expect(auditVariables(mine!.filters, "", opts).filter).toEqual({ actor: ["me"] });
  });

  it("sends the search box as search and each chip as a filter field", () => {
    const vars = auditVariables(
      { action: "org.login", target: "role_binding", decision: "DENY", since: "24h" },
      " team. ",
      opts
    );
    expect(vars.search).toBe("team.");
    expect(vars.filter).toEqual({
      action: ["org.login"],
      targetKind: ["role_binding"],
      decision: ["DENY"],
      since: "2026-09-26T14:00:00.000Z",
    });
  });

  it("leaves an unparseable since out rather than sending it", () => {
    expect(auditVariables({ since: "yesterday" }, "", opts).filter).toBeNull();
  });

  it("the Denied view sends decision DENY", () => {
    const denied = AUDIT_LIST.views.find((v) => v.key === "denied");
    expect(auditVariables(denied!.filters, "", opts).filter).toEqual({ decision: ["DENY"] });
  });
});

describe("outcomeOf", () => {
  it("reads a live deployment as succeeded, not running", () => {
    expect(outcomeOf("deployment", "running")).toBe("succeeded");
    expect(outcomeOf("deployment", "deploying")).toBe("running");
    expect(outcomeOf("deployment", "pending_approval")).toBe("waiting");
    expect(outcomeOf("deployment", "rolled_back")).toBe("failed");
  });

  it("maps the other sources' words and keeps an unknown one unknown", () => {
    expect(outcomeOf("agent", "completed")).toBe("succeeded");
    expect(outcomeOf("agent", "timed_out")).toBe("failed");
    expect(outcomeOf("job", "superseded")).toBe("cancelled");
    expect(outcomeOf("workflow", "RUNNING")).toBe("running");
    expect(outcomeOf("workflow", "exotic")).toBe("unknown");
  });
});

describe("runAuditVariables", () => {
  const base = { filters: {}, q: "", sort: RUN_AUDIT_LIST.defaultSort, pageSize: 25, after: null };

  it("asks for the newest page with no filter by default", () => {
    expect(runAuditVariables(base, NOW)).toEqual({
      filter: null,
      search: null,
      sort: "-at",
      first: 25,
      after: null,
    });
  });

  it("sends Mine as startedBy me and each chip as a filter field", () => {
    const mine = RUN_AUDIT_LIST.views.find((v) => v.key === "mine");
    expect(runAuditVariables({ ...base, filters: mine!.filters }, NOW).filter).toEqual({
      startedBy: ["me"],
    });
    expect(
      runAuditVariables(
        {
          ...base,
          filters: {
            kind: "job",
            outcome: "failed",
            trigger: "schedule",
            app: "checkout",
            since: "1h",
          },
          q: " 7e11 ",
        },
        NOW
      )
    ).toMatchObject({
      search: "7e11",
      filter: {
        kind: ["job"],
        outcome: ["failed"],
        trigger: ["schedule"],
        app: ["checkout"],
        since: "2026-09-27T13:00:00.000Z",
      },
    });
  });

  it("sends oldest first as `at` and passes the cursor through", () => {
    expect(
      runAuditVariables({ ...base, sort: [{ key: "at", dir: "asc" }], after: "c1" }, NOW)
    ).toMatchObject({ sort: "at", after: "c1" });
  });

  it("the kind filter is a declared field, so a typed token becomes a chip", () => {
    expect(RUN_AUDIT_LIST.fields.map((f) => f.key)).toContain("kind");
  });
});

describe("fromRunAuditItem", () => {
  const row: RunAuditItem = {
    kind: "deployment",
    id: "dep-1",
    subject: "checkout · production",
    scope: "checkout",
    agentSlug: "",
    workflowSlug: "",
    trigger: "webhook",
    sourceTrigger: "push",
    startedByDisplay: "grace@example.com",
    startedByMe: false,
    at: "2026-09-27T12:00:00Z",
    durationSeconds: 84,
    status: "running",
    outcome: "succeeded",
  };

  it("keeps the server's outcome and says both trigger words", () => {
    const run = fromRunAuditItem(row);
    expect(run).toMatchObject({
      key: "deployment:dep-1",
      outcome: "succeeded",
      trigger: "webhook · push",
      startedBy: "grace@example.com",
      href: "/deployments/dep-1",
    });
  });

  it("links each kind to its own detail page", () => {
    const href = (patch: Partial<RunAuditItem>) => fromRunAuditItem({ ...row, ...patch }).href;
    expect(href({ kind: "agent", id: "t1" })).toBe("/agents/runs/t1");
    expect(href({ kind: "workflow", id: "w1", workflowSlug: "nightly-sync" })).toBe(
      "/workflows/nightly-sync/runs/w1"
    );
    expect(href({ kind: "job", id: "j1" })).toBe("/jobs/runs/j1");
    expect(href({ kind: "task", id: "r1" })).toBe("/tasks/runs/r1");
  });

  it("reads an unknown trigger as none and an unknown outcome as unknown", () => {
    const run = fromRunAuditItem({ ...row, trigger: "unknown", sourceTrigger: "", outcome: "odd" });
    expect(run.trigger).toBe("");
    expect(run.outcome).toBe("unknown");
  });
});

describe("RUN_CSV", () => {
  it("writes one row per run under the header", () => {
    const csv = buildCsv(COMBINED_RUNS.slice(0, 2), RUN_CSV);
    const lines = csv.trimEnd().split("\r\n");
    expect(lines[0]).toBe(
      "Kind,Id,Subject,Scope,Started by,Trigger,At,Duration (s),Status,Outcome"
    );
    expect(lines).toHaveLength(3);
  });
});
