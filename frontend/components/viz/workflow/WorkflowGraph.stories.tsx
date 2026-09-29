import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { useSimulation } from "../core/use-simulation";
import { VizLegend } from "../core/VizLegend";
import { makeWorkflows, stepWorkflows, type WorkflowSnapshot } from "../core/workflow-model";

import { WORKFLOW_GRAPH_LEGEND, WorkflowGraph } from "./WorkflowGraph";

function Shell({
  snapshot,
  motion = "full",
  flowParticles = true,
}: {
  snapshot: WorkflowSnapshot;
  motion?: "full" | "reduced";
  flowParticles?: boolean;
}) {
  return (
    <div className="bg-card space-y-3 rounded-md border p-4">
      <WorkflowGraph snapshot={snapshot} motion={motion} flowParticles={flowParticles} />
      <VizLegend items={WORKFLOW_GRAPH_LEGEND} motion={motion} />
    </div>
  );
}

function LiveDemo({ lines = 3, trainsPerLine = 3 }: { lines?: number; trainsPerLine?: number }) {
  const initial = React.useMemo(
    () => makeWorkflows({ lines, trainsPerLine }),
    [lines, trainsPerLine]
  );
  const snapshot = useSimulation(initial, stepWorkflows, { intervalMs: 1200, seed: 7 });
  return <Shell snapshot={snapshot} />;
}

function Shaped({
  initial,
  motion = "full",
  live = true,
}: {
  initial: WorkflowSnapshot;
  motion?: "full" | "reduced";
  live?: boolean;
}) {
  const snapshot = useSimulation(initial, stepWorkflows, {
    intervalMs: 1200,
    seed: 7,
    paused: !live,
  });
  return <Shell snapshot={snapshot} motion={motion} />;
}

/** The shaped lines only: a loop, a fanout, a supervisor and a nested workflow. */
function shapes(): WorkflowSnapshot {
  return makeWorkflows({ lines: 0, shapes: true });
}

/** Feature delivery alone: Test and Review loop back to Code, Deploy retries itself. */
function featureDelivery(): WorkflowSnapshot {
  const s = shapes();
  const id = s.lines[0].id;
  return {
    ...s,
    lines: s.lines.filter((l) => l.id === id),
    trains: s.trains.filter((t) => t.lineId === id),
    segments: s.segments.filter((g) => g.lineId === id),
  };
}

function quiet(): WorkflowSnapshot {
  const s = makeWorkflows({ trainsPerLine: 0 });
  return { ...s, segments: s.segments.map((g) => ({ ...g, rate: 0, backlog: 0 })) };
}

function incident(): WorkflowSnapshot {
  const s = makeWorkflows({ backedUp: true, trainsPerLine: 4, seed: 5 });
  const trains = s.trains.map((t, i) =>
    i % 5 === 1 && s.lines.find((l) => l.id === t.lineId)!.stations[t.at].kind === "stage"
      ? { ...t, state: "failed" as const }
      : t
  );
  // Pile runs at the Release gate so it reads as waiting on a person.
  const gate = s.lines[0].stations.findIndex((st) => st.kind === "gate");
  trains.push(
    ...[0, 1, 2].map((k) => ({
      id: `held${k}`,
      lineId: s.lines[0].id,
      label: `#19${k}0`,
      at: gate,
      progress: 0,
      state: "held" as const,
      startedAt: s.now - 900_000,
    }))
  );
  return { ...s, trains };
}

function longStrings(): WorkflowSnapshot {
  const s = makeWorkflows();
  return {
    ...s,
    lines: s.lines.map((l, i) => ({
      ...l,
      name: `${l.name} for the customer-success analytics warehouse ${i}`,
      stations: l.stations.map((st) => ({
        ...st,
        name: `${st.name}-with-a-very-long-stage-identifier`,
      })),
    })),
    trains: s.trains.map((t) => ({ ...t, label: `${t.label}-release-candidate-build` })),
  };
}

const meta: Meta<typeof WorkflowGraph> = {
  title: "Viz/Workflow/WorkflowGraph",
  component: WorkflowGraph,
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj<typeof WorkflowGraph>;

export const Live: Story = { render: () => <LiveDemo /> };
export const Quiet: Story = { render: () => <Shell snapshot={quiet()} /> };
export const Incident: Story = { render: () => <Shell snapshot={incident()} /> };
export const Large: Story = { render: () => <LiveDemo lines={8} trainsPerLine={4} /> };
export const Reduced: Story = {
  render: () => <Shell snapshot={incident()} motion="reduced" />,
};
export const NoParticles: Story = {
  render: () => <Shell snapshot={makeWorkflows()} flowParticles={false} />,
};
export const LongStrings: Story = { render: () => <Shell snapshot={longStrings()} /> };
export const Narrow: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Shell snapshot={incident()} />
    </div>
  ),
};

/** Test failed and Review rejected send work back to Code on their own tracks, each with its bound. */
export const FeatureDelivery: Story = { render: () => <Shaped initial={featureDelivery()} /> };

/** Every shape at once, live: loops, a fan-out and its join, a supervisor's workers, a nested workflow. */
export const Shapes: Story = { render: () => <Shaped initial={shapes()} /> };

/** The same shapes still: tracks, bounds, branch states and busy workers read without motion. */
export const ShapesReduced: Story = {
  render: () => <Shaped initial={shapes()} motion="reduced" live={false} />,
};

export const ShapesNarrow: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Shaped initial={shapes()} />
    </div>
  ),
};

/** Classic lines and shaped lines together, as a real workspace would mix them. */
export const Mixed: Story = {
  render: () => <Shaped initial={makeWorkflows({ shapes: true })} />,
};
