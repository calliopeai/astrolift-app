import { describe, expect, it } from "vitest";

import { buildCsv } from "@/components/list/exportCsv";
import { parseListState } from "@/components/list/list-state";

import { AUDIT_LIST, auditVariables, sinceToIso } from "./audit-list";
import {
  type CombinedRun,
  filterRuns,
  mergeRuns,
  outcomeOf,
  pageRuns,
  RUN_AUDIT_LIST,
  RUN_CSV,
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
  const opts = { pageSize: 100, after: null, viewerId: "usr-1", now: NOW };

  it("sends the default state as the route's preload does", () => {
    const state = parseListState(AUDIT_LIST, "");
    expect(auditVariables(state.filters, state.q, opts).variables).toEqual({
      limit: 100,
      after: null,
      action: null,
      decision: null,
      actorId: null,
      createdAtGte: null,
      createdAtLte: null,
      includeTotal: true,
    });
  });

  it("resolves Mine to the viewer and waits until the viewer is known", () => {
    expect(auditVariables({ actor: "me" }, "", opts).variables.actorId).toBe("usr-1");
    expect(auditVariables({ actor: "me" }, "", { ...opts, viewerId: null }).ready).toBe(false);
  });

  it("uses the search box as the exact action, with an action chip winning", () => {
    expect(auditVariables({}, " team.create ", opts).variables.action).toBe("team.create");
    expect(auditVariables({ action: "org.login" }, "team", opts).variables.action).toBe(
      "org.login"
    );
  });

  it("the Denied view sends decision DENY", () => {
    const denied = AUDIT_LIST.views.find((v) => v.key === "denied");
    expect(auditVariables(denied!.filters, "", opts).variables.decision).toBe("DENY");
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

describe("mergeRuns", () => {
  it("orders newest first across sources, runs without a time last", () => {
    const run = (key: string, at: string) => ({ ...COMBINED_RUNS[0], key, at }) as CombinedRun;
    const merged = mergeRuns(
      [run("a", "2026-09-27T10:00:00Z"), run("b", "")],
      [run("c", "2026-09-27T12:00:00Z")]
    );
    expect(merged.map((r) => r.key)).toEqual(["c", "a", "b"]);
  });
});

describe("filterRuns", () => {
  it("filters by kind, outcome, Mine and search", () => {
    expect(filterRuns(COMBINED_RUNS, { kind: "job" }, "", NOW).every((r) => r.kind === "job")).toBe(
      true
    );
    expect(
      filterRuns(COMBINED_RUNS, { outcome: "failed" }, "", NOW).every((r) => r.outcome === "failed")
    ).toBe(true);
    const mine = filterRuns(COMBINED_RUNS, { startedBy: "me" }, "", NOW);
    expect(mine.length).toBeGreaterThan(0);
    expect(mine.every((r) => r.startedByMe)).toBe(true);
    expect(
      filterRuns(COMBINED_RUNS, {}, "CHECKOUT", NOW).every((r) =>
        `${r.subject} ${r.scope}`.includes("checkout")
      )
    ).toBe(true);
  });

  it("drops runs older than since", () => {
    const recent = filterRuns(COMBINED_RUNS, { since: "1h" }, "", NOW);
    expect(recent.every((r) => Date.parse(r.at) >= NOW - 3_600_000)).toBe(true);
    expect(recent.length).toBeLessThan(COMBINED_RUNS.length);
  });

  it("the kind filter is a declared field, so a typed token becomes a chip", () => {
    expect(RUN_AUDIT_LIST.fields.map((f) => f.key)).toContain("kind");
  });
});

describe("pageRuns", () => {
  it("walks offset cursors to the end", () => {
    const first = pageRuns(COMBINED_RUNS, null, 10);
    expect(first.rows).toHaveLength(10);
    expect(first.nextCursor).toBe("o:10");
    const last = pageRuns(COMBINED_RUNS, first.nextCursor, 10);
    expect(last.rows).toEqual(COMBINED_RUNS.slice(10, 20));
    expect(last.nextCursor).toBeNull();
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
