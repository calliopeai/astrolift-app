import { describe, expect, it } from "vitest";

import { effectiveFilters, parseListState } from "@/components/list/use-list-state";

import { CRON_WORKLOADS, RECENT } from "./jobs-tasks.fixtures";
import {
  COMMAND_RUNS_LIST,
  JOBS_LIST,
  narrowCommandRuns,
  outputLines,
  runFailure,
  runSteps,
  selectJobs,
  withLastRuns,
} from "./jobs-list";
import { COMMAND_RUNS } from "./jobs-tasks.fixtures";

function jobsFor(qs: string) {
  const state = parseListState(JOBS_LIST, qs);
  return selectJobs(withLastRuns(CRON_WORKLOADS, RECENT), {
    filters: effectiveFilters(JOBS_LIST, state),
    q: state.q,
    sort: state.sort,
    page: state.page,
    pageSize: state.pageSize,
  });
}

describe("JOBS_LIST", () => {
  it("leads with All and Mine, then Failing and Paused", () => {
    expect(JOBS_LIST.views.map((v) => v.label)).toEqual(["All", "Mine", "Failing", "Paused"]);
  });
});

describe("selectJobs", () => {
  it("sorts by name by default", () => {
    expect(jobsFor("").rows.map((j) => j.slug)).toEqual([
      "nightly-report",
      "sync-invoices",
      "weekly-digest",
    ]);
  });

  it("Failing keeps jobs whose latest run failed", () => {
    expect(jobsFor("view=failing").rows.map((j) => j.slug)).toEqual(["sync-invoices"]);
  });

  it("Mine and Paused hold nothing rather than everything", () => {
    expect(jobsFor("view=mine").totalCount).toBe(0);
    expect(jobsFor("view=paused").totalCount).toBe(0);
  });

  it("a job with no recent run matches Last run: never", () => {
    expect(jobsFor("lastRun=never").rows.map((j) => j.slug)).toEqual(["weekly-digest"]);
  });

  it("searches slug, app and schedule", () => {
    expect(jobsFor("q=*%2F15").rows.map((j) => j.slug)).toEqual(["sync-invoices"]);
  });
});

describe("narrowCommandRuns", () => {
  it("Mine keeps the viewer's commands, and none when the viewer is unknown", () => {
    const state = parseListState(COMMAND_RUNS_LIST, "view=mine");
    const filters = effectiveFilters(COMMAND_RUNS_LIST, state);
    expect(narrowCommandRuns(COMMAND_RUNS, filters, "leo")).toHaveLength(2);
    expect(narrowCommandRuns(COMMAND_RUNS, filters, null)).toEqual([]);
  });
});

describe("run view", () => {
  const run = {
    createdAt: "2026-09-28T02:00:00Z",
    startedAt: "2026-09-28T02:00:04Z",
    endedAt: null,
  };

  it("a running run: scheduled done, the run ticking", () => {
    const steps = runSteps(run, "running", "nightly-report", Date.parse("2026-09-28T02:01:04Z"));
    expect(steps.map((s) => s.state)).toEqual(["ok", "running"]);
    expect(steps[0].durationMs).toBe(4000);
    expect(steps[1].durationMs).toBe(60_000);
  });

  it("output becomes one line per line, all at the start time", () => {
    const lines = outputLines("a\nb\n", run.startedAt);
    expect(lines).toEqual([
      { ts: run.startedAt, message: "a" },
      { ts: run.startedAt, message: "b" },
    ]);
  });

  it("a failure says the exit code and the last line", () => {
    expect(runFailure("failed", 2, "one\ntwo\n", "")?.reason).toBe("exit 2: two");
    expect(runFailure("succeeded", 0, "x", "")).toBeNull();
  });
});
