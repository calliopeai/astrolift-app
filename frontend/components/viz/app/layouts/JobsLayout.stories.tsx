import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { makeApp, stepApp, type AppNode, type AppSnapshot } from "../../core/app-model";
import { mulberry32 } from "../../core/semantics";
import { useSimulation } from "../../core/use-simulation";
import { VizLegend } from "../../core/VizLegend";

import { JOBS_LAYOUT_LEGEND, JobsLayout, type JobRun } from "./JobsLayout";

function warm(s: AppSnapshot, steps: number, seed = 11): AppSnapshot {
  const rng = mulberry32(seed);
  for (let i = 0; i < steps; i++) s = stepApp(s, rng, 1200);
  return s;
}

function quiet(s: AppSnapshot): AppSnapshot {
  return {
    ...s,
    nodes: s.nodes.map((n) => ({
      ...n,
      load: 0,
      health: "idle",
      rps: n.rps === undefined ? undefined : 0,
    })),
    edges: s.edges.map((e) => ({ ...e, rps: 0, errorRate: 0 })),
    events: [],
  };
}

const DAY = 86_400_000;
const NOW = Date.UTC(2026, 8, 28, 14, 0, 0);
/** Nightly runs at 02:00 UTC for two weeks, one failed, one missing. */
const NIGHTLY_RUNS: JobRun[] = Array.from({ length: 14 }, (_, i) => ({
  jobId: "job",
  at: Date.UTC(2026, 8, 28, 2, 0, 20) - (13 - i) * DAY,
  ok: i !== 9,
})).filter((_, i) => i !== 4);

function Diagram() {
  return (
    <div className="text-muted-foreground flex h-32 items-center justify-center text-xs">
      Diagram slot
    </div>
  );
}

function Demo({
  initial,
  live = false,
  motion = "full",
  runs,
}: {
  initial: AppSnapshot;
  live?: boolean;
  motion?: "full" | "reduced";
  runs?: JobRun[];
}) {
  const snapshot = useSimulation(initial, stepApp, { paused: !live, seed: 5 });
  const [selected, setSelected] = React.useState<string>();
  return (
    <div className="bg-card rounded-md border">
      <JobsLayout
        snapshot={snapshot}
        motion={motion}
        runs={runs}
        onSelectNode={setSelected}
        diagram={<Diagram />}
      />
      <div className="text-muted-foreground border-t px-4 py-2 text-xs">
        Selected: {selected ?? "none"}
      </div>
      <div className="border-t px-4 py-2">
        <VizLegend items={JOBS_LAYOUT_LEGEND} motion={motion} />
      </div>
    </div>
  );
}

const meta: Meta<typeof Demo> = { title: "Viz/App/Layouts/JobsLayout", component: Demo };
export default meta;

type Story = StoryObj<typeof Demo>;

/** Scheduled: next-run countdown and the history strip. */
export const Live: Story = {
  args: { initial: warm(makeApp("scheduled", { now: NOW }), 4), live: true, runs: NIGHTLY_RUNS },
};

export const Task: Story = { args: { initial: warm(makeApp("task"), 4), live: true } };

export const Workflow: Story = { args: { initial: warm(makeApp("workflow"), 4), live: true } };

export const Mixed: Story = { args: { initial: warm(makeApp("mixed"), 8), live: true } };

export const Quiet: Story = { args: { initial: quiet(makeApp("workflow")) } };

export const Incident: Story = {
  args: { initial: warm(makeApp("workflow", { incident: true }), 6), live: true },
};

export const IncidentTask: Story = {
  args: { initial: makeApp("task", { incident: true }) },
};

function largeMixed(): AppSnapshot {
  const base = makeApp("microservices", { seed: 6 });
  const rng = mulberry32(13);
  const mk = (id: string, name: string, role: AppNode["role"], kind: string): AppNode => ({
    id,
    name,
    role,
    kind,
    health: "ok",
    load: rng() * 0.9,
    rps: role === "data" || role === "external" ? undefined : Math.round(rng() * 300),
  });
  const extra: AppNode[] = [
    ...Array.from({ length: 5 }, (_, i) =>
      mk(`fn${i}`, `webhook-${i + 1}`, "function", "function")
    ),
    ...Array.from({ length: 4 }, (_, i) => mk(`cron${i}`, `nightly-${i + 1}`, "worker", "cronjob")),
    mk("sched", "0 2 * * *", "schedule", "schedule"),
    mk("agent", "ops-agent", "agent", "agent"),
    mk("cache", "redis", "data", "redis"),
  ];
  return {
    ...base,
    topology: "mixed",
    nodes: [...base.nodes, ...extra],
    edges: [
      ...base.edges,
      ...extra
        .filter((n) => n.role === "function")
        .map((n) => ({ from: "in", to: n.id, rps: 30, errorRate: 0 })),
    ],
  };
}

export const Large: Story = { args: { initial: warm(largeMixed(), 10), live: true } };

export const Reduced: Story = {
  args: { initial: warm(makeApp("mixed", { incident: true }), 8), motion: "reduced" },
};

export const ReducedWorkflow: Story = {
  args: { initial: warm(makeApp("workflow", { incident: true }), 6), motion: "reduced" },
};

const long = makeApp("scheduled", { now: NOW });
export const LongStrings: Story = {
  args: {
    initial: {
      ...long,
      nodes: long.nodes.map((n) =>
        n.role === "schedule"
          ? n
          : { ...n, name: `${n.name}-with-an-unreasonably-long-descriptive-job-name-v2` }
      ),
    },
    runs: NIGHTLY_RUNS,
  },
};
