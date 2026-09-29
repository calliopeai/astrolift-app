import { describe, expect, it } from "vitest";

import { effectiveFilters, parseListState } from "@/components/list/list-state";

import {
  COMMAND_RUNS_LIST,
  commandRunsVariables,
  JOB_RUNS_LIST,
  jobRunsVariables,
  JOBS_LIST,
  type JobRow,
  jobsVariables,
  narrowJobs,
  outputLines,
  runFailure,
  runSteps,
} from "./jobs-list";
import { CRON_WORKLOADS } from "./jobs-tasks.fixtures";

function jobsVarsFor(qs: string, appSlug: string | null = null) {
  const state = parseListState(JOBS_LIST, qs);
  return jobsVariables(appSlug, { ...state, filters: effectiveFilters(JOBS_LIST, state) });
}

const ROWS: JobRow[] = CRON_WORKLOADS.map((j, i) => ({
  ...j,
  lastRun:
    i === 2
      ? null
      : {
          id: `run-${i}`,
          status: i === 1 ? "failed" : "succeeded",
          startedAt: null,
          createdAt: "2026-09-28T02:00:00Z",
        },
}));

describe("JOBS_LIST", () => {
  it("leads with All and Mine, then Failing and Paused", () => {
    expect(JOBS_LIST.views.map((v) => v.label)).toEqual(["All", "Mine", "Failing", "Paused"]);
  });

  it("keeps a note only on the views the server cannot answer", () => {
    expect(JOBS_LIST.views.filter((v) => v.note).map((v) => v.key)).toEqual(["failing", "paused"]);
  });
});

describe("jobsVariables", () => {
  it("asks for one numbered page of cron workloads, by name", () => {
    expect(jobsVarsFor("")).toEqual({
      appSlug: null,
      kinds: ["cronjob"],
      search: null,
      filter: null,
      sort: "name",
      page: 1,
      pageSize: 25,
    });
  });

  it("Mine is the owner, the app chip the app, both on the server", () => {
    expect(jobsVarsFor("view=mine").filter).toEqual({ owner: ["me"] });
    expect(jobsVarsFor("app=billing&q=%20nightly%20&page=2", "billing")).toMatchObject({
      appSlug: "billing",
      search: "nightly",
      filter: { app: ["billing"] },
      page: 2,
    });
  });

  it("Failing and Paused send no filter the server would refuse", () => {
    expect(jobsVarsFor("view=failing").filter).toBeNull();
    expect(jobsVarsFor("view=paused").filter).toBeNull();
  });
});

describe("narrowJobs", () => {
  const filtersFor = (qs: string) => effectiveFilters(JOBS_LIST, parseListState(JOBS_LIST, qs));

  it("Failing keeps the jobs on the page whose latest run failed", () => {
    expect(narrowJobs(ROWS, filtersFor("view=failing")).map((j) => j.slug)).toEqual([
      "sync-invoices",
    ]);
  });

  it("Paused holds nothing rather than everything; All keeps the page as served", () => {
    expect(narrowJobs(ROWS, filtersFor("view=paused"))).toEqual([]);
    expect(narrowJobs(ROWS, filtersFor(""))).toEqual(ROWS);
  });
});

describe("jobRunsVariables", () => {
  const varsFor = (qs: string) => {
    const state = parseListState(JOB_RUNS_LIST, qs);
    return jobRunsVariables(effectiveFilters(JOB_RUNS_LIST, state), state);
  };

  it("sends one page with no filter by default", () => {
    expect(varsFor("app=billing&workload=nightly-report")).toEqual({
      appSlug: "billing",
      workloadSlug: "nightly-report",
      environmentName: null,
      search: null,
      filter: null,
      limit: 25,
      after: null,
    });
  });

  it("Failed is the status filter, Mine who ran it now", () => {
    expect(varsFor("view=failed").filter).toEqual({ status: ["failed"] });
    expect(varsFor("view=mine").filter).toEqual({ triggeredBy: ["me"] });
    expect(varsFor("view=failed").limit).toBe(25);
  });

  it("has no stand-in notes left", () => {
    expect(JOB_RUNS_LIST.views.every((v) => !v.note)).toBe(true);
  });
});

describe("commandRunsVariables", () => {
  it("Mine is who invoked it, on the server, one page wide", () => {
    const state = parseListState(COMMAND_RUNS_LIST, "view=mine");
    expect(commandRunsVariables(effectiveFilters(COMMAND_RUNS_LIST, state), state)).toEqual({
      appSlug: null,
      search: null,
      filter: { invokedBy: ["me"] },
      limit: 25,
      after: null,
    });
    expect(COMMAND_RUNS_LIST.views.every((v) => !v.note)).toBe(true);
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
