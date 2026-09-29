import { describe, expect, it } from "vitest";

import { mulberry32 } from "./semantics";
import {
  makeFeatureTimeline,
  makeWorkflows,
  shapedLines,
  stepWorkflows,
  type FanoutStation,
  type SupervisorStation,
  type WorkflowLine,
  type WorkflowSnapshot,
  type WorkflowTrain,
} from "./workflow-model";
import { joinReady, loopRound } from "./workflow-shapes";

function run(s: WorkflowSnapshot, steps: number, seed = 3): WorkflowSnapshot[] {
  const rng = mulberry32(seed);
  const out: WorkflowSnapshot[] = [];
  for (let i = 0; i < steps; i++) out.push((s = stepWorkflows(s, rng, 1200)));
  return out;
}

/** A snapshot holding one line and one run on it. */
function solo(line: WorkflowLine, train: Partial<WorkflowTrain>, extra: WorkflowLine[] = []) {
  return {
    now: 0,
    lines: [line, ...extra],
    segments: [],
    trains: [
      {
        id: "r",
        lineId: line.id,
        label: "#1",
        at: 0,
        progress: 0,
        state: "moving" as const,
        startedAt: 0,
        round: 1,
        attempt: 1,
        ...train,
      },
    ],
  };
}

const [feature, research, support, close, reconcile] = shapedLines(0);

describe("workflow model: classic lines", () => {
  it("keeps the classic lines unchanged when shapes are off", () => {
    const s = makeWorkflows();
    expect(s.lines.map((l) => l.name)).toEqual(["Release", "Lead outreach", "Nightly data"]);
    const next = stepWorkflows(s, mulberry32(1), 1200);
    expect(next.lines).toBe(s.lines);
  });

  it("adds the shaped lines after the classic ones without moving the classic runs", () => {
    const plain = makeWorkflows();
    const shaped = makeWorkflows({ shapes: true });
    expect(shaped.lines.map((l) => l.name)).toEqual([
      "Release",
      "Lead outreach",
      "Nightly data",
      "Feature delivery",
      "Research digest",
      "Support triage",
      "Quarterly close",
      "Reconcile",
    ]);
    expect(shaped.trains.slice(0, plain.trains.length)).toEqual(plain.trains);
    expect(new Set(shaped.lines.map((l) => l.id)).size).toBe(shaped.lines.length);
  });

  it("is deterministic by seed", () => {
    const a = run(makeWorkflows({ shapes: true }), 40, 9).at(-1);
    const b = run(makeWorkflows({ shapes: true }), 40, 9).at(-1);
    expect(a).toEqual(b);
  });
});

describe("workflow model: shaped fixtures", () => {
  it("starts with each shape showing", () => {
    const s = makeWorkflows({ lines: 0, shapes: true });
    const onRound2 = s.trains.find((t) => t.lineId === "l0" && t.round === 2)!;
    expect(onRound2.at).toBe(2);
    expect(s.trains.some((t) => t.onLoopId === "l0:review-rejected")).toBe(true);
    const fan = s.lines[1].stations[1] as FanoutStation;
    expect(fan.dynamic).toBe(true);
    expect(fan.branches).toHaveLength(6);
    expect(joinReady(fan).ready).toBe(false);
    const sup = s.lines[2].stations[1] as SupervisorStation;
    expect(sup.workers).toHaveLength(5);
    expect(sup.workers.filter((w) => w.busy)).toHaveLength(2);
    const child = s.trains.find((t) => t.parentRunId);
    expect(child?.lineId).toBe("l4");
    expect(s.lines[4].parent).toEqual({ lineId: "l3", stationId: "l3s1" });
  });

  it("sends a failed test back along the loop and counts the round", () => {
    const frames = run(solo(feature, { at: 2, progress: 0.99 }), 400, 5);
    const onLoop = frames.findIndex((f) => f.trains[0].onLoopId === feature.loops![0].id);
    expect(onLoop).toBeGreaterThanOrEqual(0);
    const back = frames.slice(onLoop).find((f) => !f.trains[0].onLoopId)!.trains[0];
    expect(back.at).toBe(1);
    expect(back.round).toBe(2);
    expect(back.loopRounds?.[feature.loops![0].id]).toBe(1);
  });

  it("fails a run that needs one more round than the bound allows", () => {
    const loop = feature.loops![0];
    // Round 5 of 5 on Test: the next failure has nowhere to go.
    const start = { at: 2, progress: 0.99, round: 5, loopRounds: { [loop.id]: 4 } };
    for (let seed = 1; seed < 40; seed++) {
      const next = stepWorkflows(solo(feature, start), mulberry32(seed), 1200).trains[0];
      if (next.at === 2 && next.state === "failed") {
        expect(loopRound(next, loop)).toBe(5);
        expect(next.onLoopId).toBeUndefined();
        return;
      }
    }
    throw new Error("no seed failed the test on its last round");
  });

  it("retries Deploy onto itself, bounded by attempts", () => {
    const retry = feature.loops![2];
    const seen = new Set<number>();
    for (let seed = 1; seed < 30; seed++) {
      const frames = run(solo(feature, { at: 4, progress: 0.99 }), 60, seed);
      for (const f of frames) {
        const t = f.trains[0];
        if (t.onLoopId === retry.id) expect(t.at).toBe(5);
        if (t.at === 5) seen.add(t.attempt ?? 1);
        expect(t.attempt ?? 1).toBeLessThanOrEqual(retry.maxRounds);
      }
    }
    expect(seen.has(2)).toBe(true);
  });

  it("holds the run until every branch settles, then joins", () => {
    const frames = run(solo(research, { at: 0, progress: 0.99 }), 200, 4);
    const fanning = frames.filter((f) => f.trains[0].at === 1 && f.trains[0].state === "moving");
    expect(fanning.length).toBeGreaterThan(1);
    const counts = fanning.map((f) => joinReady(f.lines[0].stations[1] as FanoutStation).settled);
    // Branches settle at different times: the settled count climbs in steps.
    expect(new Set(counts).size).toBeGreaterThan(2);
    // While the run waits at the fanout, the join is never ready; it joins the step the last settles.
    fanning.forEach((f) =>
      expect(joinReady(f.lines[0].stations[1] as FanoutStation).ready).toBe(false)
    );
    const joined = frames.findIndex((f) => f.trains[0].at === 2);
    expect(joined).toBeGreaterThan(0);
    expect(joinReady(frames[joined].lines[0].stations[1] as FanoutStation).ready).toBe(true);
  });

  it("queues a second run short of a fanout another run holds", () => {
    const s = solo(research, { at: 1 });
    const fan = s.lines[0].stations[1] as FanoutStation;
    const held: WorkflowSnapshot = {
      ...s,
      lines: [
        {
          ...research,
          stations: research.stations.map((st, i) =>
            i === 1
              ? { ...fan, runId: "r", branches: [{ id: "b", label: "B", state: "running" }] }
              : st
          ),
        },
      ],
      trains: [...s.trains, { ...s.trains[0], id: "q", at: 0, progress: 0.99 }],
    };
    const next = stepWorkflows(held, mulberry32(2), 1200);
    const q = next.trains.find((t) => t.id === "q")!;
    expect(q).toMatchObject({ at: 1, state: "held" });
    expect((next.lines[0].stations[1] as FanoutStation).runId).toBe("r");
  });

  it("has supervisor workers pick up and finish the run's sub-tasks", () => {
    const frames = run(solo(support, { at: 0, progress: 0.99 }), 200, 6);
    const busy = frames.some((f) =>
      (f.lines[0].stations[1] as SupervisorStation).workers.some((w) => w.busy && w.runId === "r")
    );
    expect(busy).toBe(true);
    const left = frames.findIndex((f) => f.trains[0].at === 2);
    const at1 = frames.slice(0, left).filter((f) => f.trains[0].at === 1 && f.trains[0].subtasks);
    expect(at1.length).toBeGreaterThan(1);
    const dones = at1.map((f) => f.trains[0].subtasks!.done);
    expect(dones).toEqual([...dones].sort((a, b) => a - b));
    expect(frames.some((f) => f.trains[0].at === 2)).toBe(true);
    frames.forEach((f) =>
      (f.lines[0].stations[1] as SupervisorStation).workers.forEach((w) => {
        expect(w.load).toBeGreaterThanOrEqual(0);
        expect(w.load).toBeLessThanOrEqual(1);
      })
    );
  });

  it("runs a nested child workflow and continues when it finishes", () => {
    const frames = run(solo(close, { at: 0, progress: 0.99 }, [reconcile]), 300, 8);
    const spawned = frames.find((f) => f.trains.some((t) => t.parentRunId === "r"));
    expect(spawned?.trains[0].childRunId).toBe("r/child");
    const past = frames.findIndex((f) => f.trains[0].at === 2);
    expect(past).toBeGreaterThan(0);
    // The parent moved only once its child had finished, and collected it.
    const last = frames[past - 1].trains.find((t) => t.parentRunId === "r")!;
    expect(last.state).toBe("done");
    expect(frames[past].trains.some((t) => t.parentRunId === "r")).toBe(false);
  });
});

describe("feature delivery timeline", () => {
  it("went round three times and finished", () => {
    const t = makeFeatureTimeline();
    expect(t.finishedAt).not.toBeNull();
    const loops = t.stages.filter((s) => s.causedBy);
    expect(loops.map((s) => s.causedBy!.loopId)).toEqual([
      "l3:test-failed",
      "l3:test-failed",
      "l3:review-rejected",
    ]);
    expect(Math.max(...t.stages.map((s) => s.round ?? 1))).toBe(4);
    expect(t.stages.at(-1)).toMatchObject({ name: "Deploy", status: "succeeded" });
    for (let i = 1; i < t.stages.length; i++)
      expect(t.stages[i].startedAt).toBe(t.stages[i - 1].finishedAt);
  });
});
