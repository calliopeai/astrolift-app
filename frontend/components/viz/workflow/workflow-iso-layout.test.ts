import { describe, expect, it } from "vitest";

import type { WorkflowSnapshot } from "../core/workflow-model";
import { makeWorkflows } from "../core/workflow-model";

import {
  BACKLOG_WARN,
  QUEUE_GAP,
  STEP,
  easeOut,
  isoViewBox,
  placeCrates,
  stationHealth,
  summarize,
  truncate,
} from "./workflow-iso-layout";

function snap(partial: Partial<WorkflowSnapshot>): WorkflowSnapshot {
  return {
    now: 0,
    lines: [
      {
        id: "l0",
        name: "Release",
        stations: [
          { id: "a", name: "Build", kind: "stage" },
          { id: "b", name: "Approve", kind: "gate" },
          { id: "c", name: "Ship", kind: "stage" },
        ],
      },
    ],
    trains: [],
    segments: [],
    ...partial,
  };
}

const train = (
  id: string,
  at: number,
  state: "moving" | "held" | "failed" | "done",
  progress = 0
) => ({
  id,
  lineId: "l0",
  label: id,
  at,
  progress,
  state,
  startedAt: 0,
});

describe("stationHealth", () => {
  it("is idle with nothing there, ok with a run leaving", () => {
    const s = snap({});
    expect(stationHealth(s, s.lines[0], 0)).toBe("idle");
    const busy = snap({ trains: [train("t", 0, "moving", 0.3)] });
    expect(stationHealth(busy, busy.lines[0], 0)).toBe("ok");
  });

  it("a gate is degraded only while it holds a run", () => {
    const held = snap({ trains: [train("t", 1, "held")] });
    expect(stationHealth(held, held.lines[0], 1)).toBe("degraded");
    const moving = snap({ trains: [train("t", 1, "moving", 0.5)] });
    expect(stationHealth(moving, moving.lines[0], 1)).toBe("idle");
  });

  it("a failed run beats everything; a backed-up intake is degraded", () => {
    const failed = snap({ trains: [train("t", 2, "failed")] });
    expect(stationHealth(failed, failed.lines[0], 2)).toBe("failing");
    const backed = snap({
      segments: [{ lineId: "l0", from: 1, rate: 0.2, backlog: BACKLOG_WARN }],
    });
    expect(stationHealth(backed, backed.lines[0], 2)).toBe("degraded");
  });
});

describe("placeCrates", () => {
  it("moving crates advance with progress between stations", () => {
    const s = snap({ trains: [train("a", 0, "moving", 0), train("b", 0, "moving", 1)] });
    const p = placeCrates(s);
    expect(p.get("a")!.x).toBeLessThan(p.get("b")!.x);
    expect(p.get("b")!.x).toBeLessThan(STEP);
  });

  it("held crates wait before the booth and queue back up the belt", () => {
    const s = snap({ trains: [train("a", 1, "held"), train("b", 1, "held")] });
    const p = placeCrates(s);
    expect(p.get("a")!.x).toBeLessThan(STEP);
    expect(p.get("a")!.x - p.get("b")!.x).toBeCloseTo(QUEUE_GAP);
  });

  it("places every train of a generated snapshot", () => {
    const s = makeWorkflows({ lines: 8, trainsPerLine: 4 });
    expect(placeCrates(s).size).toBe(s.trains.length);
  });
});

describe("helpers", () => {
  it("viewBox is positive and grows with lines", () => {
    const a = isoViewBox(makeWorkflows({ lines: 1 }), 20);
    const b = isoViewBox(makeWorkflows({ lines: 8 }), 20);
    expect(a.w).toBeGreaterThan(0);
    expect(b.h).toBeGreaterThan(a.h);
  });

  it("summarize counts states", () => {
    const s = snap({ trains: [train("a", 1, "held"), train("b", 2, "failed")] });
    expect(summarize(s)).toBe("1 workflow, 2 runs, 0 moving, 1 held at gates, 1 failed");
  });

  it("truncate and easeOut", () => {
    expect(truncate("Transform", 20)).toBe("Transform");
    expect(truncate("Transformation pipeline", 8)).toBe("Transfo…");
    expect(easeOut(0)).toBe(0);
    expect(easeOut(1)).toBe(1);
    expect(easeOut(2)).toBe(1);
  });
});
