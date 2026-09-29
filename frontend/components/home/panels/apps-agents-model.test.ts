import { describe, expect, it } from "vitest";

import {
  deployReason,
  failingDeploys,
  newestFirst,
  runningSnapshot,
  runReason,
  runsPerDay,
  spendPerDay,
  workflowRunReason,
} from "./apps-agents-model";

const NOW = Date.UTC(2026, 8, 28, 14, 0, 0);

describe("newestFirst", () => {
  it("orders by time, sorts rows with no time last, and keeps ties stable", () => {
    const rows = [
      { k: "a", at: "2026-09-27T00:00:00Z" },
      { k: "b", at: "" },
      { k: "c", at: "2026-09-28T00:00:00Z" },
      { k: "d", at: "2026-09-27T00:00:00Z" },
    ];
    expect(newestFirst(rows, (r) => r.at).map((r) => r.k)).toEqual(["c", "a", "d", "b"]);
  });
});

describe("failingDeploys", () => {
  const d = (id: string, app: string, env: string, status: string, at: string) => ({
    id,
    registeredAppSlug: app,
    environmentName: env,
    status,
    createdAt: at,
    startedAt: at,
  });

  it("keeps an app and environment only when its newest deploy failed", () => {
    const out = failingDeploys([
      d("1", "billing", "prod", "failed", "2026-09-28T10:00:00Z"),
      d("2", "checkout", "prod", "failed", "2026-09-28T09:00:00Z"),
      d("3", "checkout", "prod", "running", "2026-09-28T11:00:00Z"),
      d("4", "checkout", "staging", "rolled_back", "2026-09-28T08:00:00Z"),
    ] as never[]);
    expect(out.map((x: { id: string }) => x.id)).toEqual(["1", "4"]);
  });
});

describe("reasons", () => {
  it("reads a deploy's abort, then status reason, then build error, first line only", () => {
    const base = { status: "failed", abortedReason: "", statusReason: "", buildError: "" };
    expect(deployReason({ ...base, abortedReason: "Aborted by ops" } as never)).toBe(
      "Aborted by ops"
    );
    expect(deployReason({ ...base, buildError: "npm ERR!\nstack" } as never)).toBe("npm ERR!");
    expect(deployReason(base as never)).toBe("No reason was recorded for this failure.");
    expect(deployReason({ ...base, status: "rolled_back" } as never)).toBe("Rolled back.");
  });

  it("says so when a run gave no reason", () => {
    expect(runReason(null)).toBe("No reason was recorded for this run.");
    expect(runReason("  OOMKilled\nmore")).toBe("OOMKilled");
  });

  it("says where a workflow run stopped", () => {
    expect(
      workflowRunReason({ status: "failed", currentStageOrder: 2, currentStageRole: "extract" })
    ).toBe("Failed at stage 2 (extract).");
    expect(
      workflowRunReason({ status: "timed_out", currentStageOrder: null, currentStageRole: "" })
    ).toBe("Timed out.");
  });
});

describe("runsPerDay and spendPerDay", () => {
  it("buckets runs into the last seven UTC days, oldest first", () => {
    const days = runsPerDay(
      [
        { status: "completed", createdAt: "2026-09-28T01:00:00Z" },
        { status: "failed", createdAt: "2026-09-28T02:00:00Z" },
        { status: "timed_out", createdAt: "2026-09-22T23:00:00Z" },
        { status: "completed", createdAt: "2026-09-21T23:00:00Z" },
        { status: "completed", createdAt: "not a date" },
      ],
      NOW
    );
    expect(days.map((d) => d.date)).toEqual([
      "2026-09-22",
      "2026-09-23",
      "2026-09-24",
      "2026-09-25",
      "2026-09-26",
      "2026-09-27",
      "2026-09-28",
    ]);
    expect(days[0]).toEqual({ date: "2026-09-22", runs: 1, failed: 1 });
    expect(days[6]).toEqual({ date: "2026-09-28", runs: 2, failed: 1 });
  });

  it("lays the cost trend onto the same days, zero where there is no point", () => {
    const days = runsPerDay([], NOW);
    expect(spendPerDay([{ date: "2026-09-27", amountCents: 500 }], days)).toEqual([
      0, 0, 0, 0, 0, 500, 0,
    ]);
  });
});

describe("runningSnapshot", () => {
  const agents = [
    {
      id: "a1",
      slug: "bot",
      name: "bot",
      projectSlug: "p",
      runningCount: 1,
      runPaused: false,
      lastRunStatus: "running",
    },
    {
      id: "a2",
      slug: "idle",
      name: "idle",
      projectSlug: null,
      runningCount: 0,
      runPaused: false,
      lastRunStatus: "failed",
    },
  ];

  it("draws only agents with a run in flight, and the running runs as the manifest", () => {
    const s = runningSnapshot(
      agents,
      [
        { id: "t1", agentSlug: "bot", startedAt: "2026-09-28T13:59:00Z" },
        { id: "t2", agentSlug: "bot", startedAt: null },
        { id: "t3", agentSlug: "unknown", startedAt: null },
      ],
      NOW
    );
    expect(s.agents.map((a) => [a.id, a.activeRuns])).toEqual([["a1", 2]]);
    expect(s.clusters.map((c) => c.name)).toEqual(["p"]);
    expect(s.runs.map((r) => r.id)).toEqual(["t1", "t2"]);
    expect(s.events).toEqual([]);
  });

  it("is empty when nothing runs", () => {
    const s = runningSnapshot(
      agents.map((a) => ({ ...a, runningCount: 0 })),
      [],
      NOW
    );
    expect(s.agents).toEqual([]);
  });
});
