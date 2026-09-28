import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { useSimulation } from "../core/use-simulation";
import { VizLegend } from "../core/VizLegend";
import { makeFeatureTimeline, makeTimeline, type RunTimeline } from "../core/workflow-model";

import { makeFanoutTimeline, makeLiveRun, stepLiveRun } from "./replay";
import { RUN_REPLAY_LEGEND, RunReplay, type RunReplayProps } from "./RunReplay";

const NOW = Date.UTC(2026, 8, 28, 14, 0, 0);

function Shell(props: RunReplayProps) {
  return (
    <div className="max-w-3xl rounded-md border">
      <RunReplay {...props} />
      <div className="border-t px-4 py-2">
        <VizLegend items={RUN_REPLAY_LEGEND} motion={props.motion} />
      </div>
    </div>
  );
}

function LiveDemo({ motion }: { motion: "full" | "reduced" }) {
  const live = useSimulation(makeLiveRun(NOW), stepLiveRun, { intervalMs: 1000, seed: 7 });
  return <Shell timeline={live.timeline} now={live.now} motion={motion} />;
}

/** A long pipeline: 40 stages over about two hours, one 25-minute stall at a gate. */
function largeTimeline(): RunTimeline {
  let t = NOW - 2 * 3_600_000;
  const startedAt = t;
  const stages: RunTimeline["stages"] = Array.from({ length: 40 }, (_, i) => {
    const gate = i % 10 === 9;
    const d = i === 19 ? 1_500_000 : gate ? 240_000 : 45_000 + ((i * 37_000) % 180_000);
    const s = t;
    t += d;
    return {
      id: `st${i}`,
      name: gate ? `Gate ${i + 1}` : `Step ${i + 1}`,
      kind: gate ? "gate" : "stage",
      startedAt: s,
      finishedAt: t,
      status: "succeeded",
      decidedBy: gate ? "release-manager@example.com" : undefined,
    };
  });
  return { runId: "run-9002", label: "Monorepo release · #9002", startedAt, finishedAt: t, stages };
}

/** Build, Test and Scan done; Approve opened at 5m 47s and is still waiting. */
function heldAtGate(): RunTimeline {
  const startedAt = NOW - 600_000;
  const plan: [string, number][] = [
    ["Build", 94_000],
    ["Test", 212_000],
    ["Scan", 41_000],
  ];
  let t = startedAt;
  const stages: RunTimeline["stages"] = plan.map(([name, d], i) => {
    const s = t;
    t += d;
    return { id: `st${i}`, name, kind: "stage", startedAt: s, finishedAt: t, status: "succeeded" };
  });
  stages.push({
    id: "st3",
    name: "Approve",
    kind: "gate",
    startedAt: t,
    finishedAt: null,
    status: "waiting",
  });
  return { runId: "run-4822", label: "Release 3.4 · #4822", startedAt, finishedAt: null, stages };
}

function longStrings(): RunTimeline {
  const base = makeTimeline({ now: NOW });
  return {
    ...base,
    label:
      "Release 3.4.0-rc.12 of the customer-facing billing reconciliation service · #4821 (hotfix branch)",
    stages: base.stages.map((s) => ({
      ...s,
      name: `${s.name} the entire production fleet across every region and availability zone`,
      decidedBy: s.decidedBy
        ? "a.very.long.reviewer.name.from.the.platform.governance.team@example.com"
        : undefined,
    })),
  };
}

const meta: Meta<typeof Shell> = {
  title: "Viz/Workflow/RunReplay",
  component: Shell,
  args: { timeline: makeTimeline({ now: NOW }), motion: "full" },
};
export default meta;

type Story = StoryObj<typeof Shell>;

/** A run in progress: stages open as the simulation advances, the gate holds until approved. */
export const Live: Story = { render: (args) => <LiveDemo motion={args.motion} /> };

/** A finished, clean run: nothing failed, the one long block is the approval wait. */
export const Quiet: Story = {};

/** Canary failed: it settles red, and every stage after it is skipped (dashed, never lit). */
export const Incident: Story = { args: { timeline: makeTimeline({ now: NOW, failedAt: 4 }) } };

/** A run still going, held at its gate for 4m 13s: the gate breathes amber at the live edge. */
export const HeldAtGate: Story = { args: { timeline: heldAtGate(), now: NOW } };

/** Forty stages over two hours; the 25-minute stall stands out by width alone. */
export const Large: Story = { args: { timeline: largeTimeline() } };

/** No auto-play: the run opens settled at its end; drag or use the arrow keys to scrub. */
export const Reduced: Story = { args: { motion: "reduced" } };

export const LongStrings: Story = { args: { timeline: longStrings() } };

/** Feature delivery went round three times (Test failed twice, Review rejected once), then shipped. */
export const Rounds: Story = { args: { timeline: makeFeatureTimeline({ now: NOW }) } };

/** Research digest fanned out to six sources: parallel bars under the track, one failed. */
export const FanOut: Story = { args: { timeline: makeFanoutTimeline({ now: NOW }) } };

/** The rounds still: opens settled, every round listed and every loop mark in place. */
export const RoundsReduced: Story = {
  args: { timeline: makeFeatureTimeline({ now: NOW }), motion: "reduced" },
};

export const RoundsNarrow: Story = {
  render: (args) => (
    <div style={{ width: 768 }}>
      <Shell {...args} timeline={makeFeatureTimeline({ now: NOW })} />
    </div>
  ),
};

export const FanOutReduced: Story = {
  args: { timeline: makeFanoutTimeline({ now: NOW }), motion: "reduced" },
};
