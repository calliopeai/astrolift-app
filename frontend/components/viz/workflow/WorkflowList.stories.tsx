import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { useSimulation } from "../core/use-simulation";
import { VizLegend } from "../core/VizLegend";
import { makeWorkflows, stepWorkflows, type WorkflowSnapshot } from "../core/workflow-model";

import { WORKFLOW_LIST_LEGEND, WorkflowList } from "./WorkflowList";

function List({
  initial,
  motion = "full",
  live = true,
}: {
  initial: WorkflowSnapshot;
  motion?: "full" | "reduced";
  live?: boolean;
}) {
  const snapshot = useSimulation(initial, stepWorkflows, { paused: !live, seed: 7 });
  const [selected, setSelected] = React.useState<string>();
  return (
    <div className="bg-card space-y-3 rounded-md border p-4">
      <WorkflowList
        snapshot={snapshot}
        motion={motion}
        onSelectRun={setSelected}
        onSelectStation={(_line, station) => setSelected(station)}
      />
      <p className="text-muted-foreground text-xs">Selected: {selected ?? "none"}</p>
      <VizLegend items={WORKFLOW_LIST_LEGEND} motion={motion} />
    </div>
  );
}

const meta: Meta<typeof List> = {
  title: "Viz/Workflow/WorkflowList",
  component: List,
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj<typeof List>;

function incident(): WorkflowSnapshot {
  const s = makeWorkflows({ backedUp: true, trainsPerLine: 4, seed: 5 });
  return {
    ...s,
    trains: s.trains.map((t, i) => (i % 3 === 0 ? { ...t, state: "failed" as const } : t)),
  };
}

function shapes(): WorkflowSnapshot {
  return makeWorkflows({ lines: 0, shapes: true });
}

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

export const Live: Story = { render: () => <List initial={makeWorkflows()} /> };

export const Quiet: Story = {
  render: () => <List initial={makeWorkflows({ trainsPerLine: 0 })} live={false} />,
};

export const Incident: Story = { render: () => <List initial={incident()} live={false} /> };

export const Large: Story = {
  render: () => <List initial={makeWorkflows({ lines: 8, trainsPerLine: 4 })} />,
};

export const Reduced: Story = {
  render: () => <List initial={incident()} motion="reduced" live={false} />,
};

export const LongStrings: Story = {
  render: () => {
    const s = makeWorkflows({ lines: 2 });
    return (
      <List
        live={false}
        initial={{
          ...s,
          lines: s.lines.map((l) => ({
            ...l,
            name: `${l.name} for the enterprise customer onboarding programme`,
            stations: l.stations.map((st) => ({ ...st, name: `${st.name} with extended checks` })),
          })),
          trains: s.trains.map((t) => ({ ...t, label: `${t.label}-hotfix-rollback-candidate` })),
        }}
      />
    );
  },
};

/** Feature delivery: every stage with its round against the bound, each loop said in words. */
export const FeatureDelivery: Story = { render: () => <List initial={featureDelivery()} /> };

/** Every shape: fan-out progress, the join, supervisor sub-tasks, the nested child line. */
export const Shapes: Story = { render: () => <List initial={shapes()} /> };

export const ShapesReduced: Story = {
  render: () => <List initial={shapes()} motion="reduced" live={false} />,
};

export const ShapesNarrow: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <List initial={shapes()} live={false} />
    </div>
  ),
};
