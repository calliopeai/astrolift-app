import { describe, expect, it } from "vitest";

import { makeFleet, type FleetRun } from "../core/fleet-model";
import { mulberry32 } from "../core/semantics";

import {
  ARRIVALS_MS,
  LIFTOFF_MS,
  boardOrder,
  formatSpan,
  makeRuns,
  runClock,
  runDetail,
  stepRuns,
  summarizeRuns,
} from "./manifest";

const NOW = Date.UTC(2026, 8, 28, 14, 0, 0);
const run = (over: Partial<FleetRun>): FleetRun => ({
  id: "r0",
  agentId: "a0",
  label: "x",
  state: "queued",
  ...over,
});

describe("clock and detail", () => {
  it("formats spans, clamping negatives", () => {
    expect(formatSpan(45_000)).toBe("00:45");
    expect(formatSpan(3_725_000)).toBe("1:02:05");
    expect(formatSpan(-5_000)).toBe("00:00");
  });

  it("counts down while waiting, then shows UTC clock times", () => {
    expect(runClock(run({ etaAt: NOW + 90_000 }), NOW)).toBe("T-01:30");
    expect(runClock(run({ state: "holding", etaAt: NOW - 1 }), NOW)).toBe("T-00:00");
    expect(runClock(run({ state: "in_flight", startedAt: NOW }), NOW)).toBe("14:00:00");
    expect(runClock(run({ state: "landed", finishedAt: NOW + 61_000 }), NOW)).toBe("14:01:01");
  });

  it("gives the hold reason or the elapsed flight time", () => {
    expect(runDetail(run({ state: "holding", holdReason: "GPU quota" }), NOW)).toBe("GPU quota");
    expect(runDetail(run({ state: "in_flight", startedAt: NOW - 252_000 }), NOW)).toBe("04:12");
    expect(runDetail(run({ state: "landed" }), NOW)).toBeUndefined();
  });

  it("summarizes the board", () => {
    expect(summarizeRuns([])).toBe("0 runs on the board");
    expect(summarizeRuns([run({}), run({ id: "r1", state: "failed" }), run({ id: "r2" })])).toBe(
      "3 runs: 2 queued, 1 failed"
    );
  });

  it("orders by scheduled time, stably", () => {
    const rs = [run({ id: "b", etaAt: 2 }), run({ id: "a", etaAt: 2 }), run({ id: "c", etaAt: 1 })];
    expect(boardOrder(rs).map((r) => r.id)).toEqual(["c", "a", "b"]);
  });
});

describe("run queue simulation", () => {
  const fleet = makeFleet({ now: NOW });

  it("makes deterministic runs honouring the mix", () => {
    const a = makeRuns(fleet, { count: 20, seed: 4, mix: { holding: 1 } });
    expect(a).toEqual(makeRuns(fleet, { count: 20, seed: 4, mix: { holding: 1 } }));
    expect(a.every((r) => r.state === "holding" && r.holdReason)).toBe(true);
  });

  it("lifts off at T-0, goes in flight after lift-off, rolls finished runs off", () => {
    const snap = {
      ...fleet,
      runs: [
        run({ id: "r0", etaAt: NOW }),
        run({ id: "r1", state: "lifted_off", startedAt: NOW - LIFTOFF_MS }),
        run({ id: "r2", state: "landed", finishedAt: NOW - ARRIVALS_MS }),
      ],
    };
    const next = stepRuns(snap, () => 0.99);
    expect(next.runs.find((r) => r.id === "r0")).toMatchObject({
      state: "lifted_off",
      startedAt: NOW,
    });
    expect(next.runs.find((r) => r.id === "r1")?.state).toBe("in_flight");
    expect(next.runs.find((r) => r.id === "r2")).toBeUndefined();
    // The board keeps its length with a newly queued run.
    expect(next.runs).toHaveLength(3);
    expect(next.runs.find((r) => r.id === "r3")?.state).toBe("queued");
  });

  it("stays consistent over many steps", () => {
    const rng = mulberry32(9);
    let s = { ...fleet, runs: makeRuns(fleet, { count: 12 }) };
    for (let i = 0; i < 200; i++) s = stepRuns({ ...s, now: s.now + 1000 }, rng);
    expect(s.runs).toHaveLength(12);
    expect(new Set(s.runs.map((r) => r.id)).size).toBe(12);
    for (const r of s.runs) {
      if (r.state === "holding") expect(r.holdReason).toBeTruthy();
      if (r.state === "landed" || r.state === "failed") expect(r.finishedAt).toBeDefined();
    }
  });
});
