import { describe, expect, it } from "vitest";

import { agentFleetSnapshot, agentHealth } from "./agent-fleet-snapshot";

const base = { runningCount: 0, runPaused: false, lastRunStatus: null, projectSlug: null };

describe("agentHealth", () => {
  it("reads a failed last run as failing, a running agent as ok, the rest idle", () => {
    expect(agentHealth({ ...base, id: "a", name: "a", lastRunStatus: "timed_out" })).toBe(
      "failing"
    );
    expect(agentHealth({ ...base, id: "a", name: "a", runningCount: 2 })).toBe("ok");
    expect(agentHealth({ ...base, id: "a", name: "a", runPaused: true })).toBe("idle");
  });
});

describe("agentFleetSnapshot", () => {
  const snap = agentFleetSnapshot(
    [
      { ...base, id: "a1", name: "triage", projectSlug: "support", runningCount: 6 },
      { ...base, id: "a2", name: "scout", projectSlug: "support" },
      { ...base, id: "a3", name: "janitor" },
    ],
    "a1",
    [
      {
        id: "t1-aaaaaaaa",
        status: "running",
        createdAt: "",
        startedAt: "2026-09-28T12:00:00Z",
        finishedAt: null,
      },
      { id: "t2", status: "cancelled", createdAt: "", startedAt: null, finishedAt: null },
    ],
    1000
  );

  it("groups agents by project, with one group for agents that have none", () => {
    expect(snap.clusters.map((c) => c.name)).toEqual(["support", "No project"]);
    expect(snap.agents.map((a) => a.clusterId)).toEqual(["support", "support", "__none"]);
  });

  it("caps load at 1 and carries the running count", () => {
    expect(snap.agents[0]).toMatchObject({ load: 1, activeRuns: 6 });
  });

  it("maps this agent's runs onto manifest states and drops the ones it cannot draw", () => {
    expect(snap.runs).toEqual([
      {
        id: "t1-aaaaaaaa",
        agentId: "a1",
        label: "t1-aaaaa",
        state: "in_flight",
        startedAt: Date.parse("2026-09-28T12:00:00Z"),
        finishedAt: undefined,
      },
    ]);
    expect(snap.now).toBe(1000);
  });
});
