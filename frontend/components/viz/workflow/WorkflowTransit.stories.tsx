import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { useSimulation } from "../core/use-simulation";
import { VizLegend } from "../core/VizLegend";
import {
  makeWorkflows,
  stepWorkflows,
  type WorkflowSnapshot,
  type WorkflowViewProps,
} from "../core/workflow-model";

import { WORKFLOW_TRANSIT_LEGEND, WorkflowTransit } from "./WorkflowTransit";

function Shell(props: WorkflowViewProps) {
  return (
    <div className="bg-card max-w-3xl space-y-3 rounded-md border p-4">
      <WorkflowTransit {...props} />
      <VizLegend items={WORKFLOW_TRANSIT_LEGEND} motion={props.motion} />
    </div>
  );
}

function Simulated({
  initial,
  ...rest
}: Omit<WorkflowViewProps, "snapshot"> & { initial: WorkflowSnapshot }) {
  const snapshot = useSimulation(initial, stepWorkflows, { intervalMs: 1200, seed: 7 });
  return <Shell {...rest} snapshot={snapshot} />;
}

function quiet(): WorkflowSnapshot {
  const s = makeWorkflows({ trainsPerLine: 0 });
  return { ...s, segments: s.segments.map((seg) => ({ ...seg, rate: 0, backlog: 0 })) };
}

function incident(): WorkflowSnapshot {
  const s = makeWorkflows({ backedUp: true, trainsPerLine: 4, seed: 5 });
  return {
    ...s,
    trains: s.trains.map((t, i) => {
      const line = s.lines.find((l) => l.id === t.lineId)!;
      const gate = line.stations.findIndex((st) => st.kind === "gate");
      if (i % 4 === 0) return { ...t, state: "failed" as const, progress: 0 };
      if (i % 4 === 1 && gate >= 0) return { ...t, at: gate, progress: 0, state: "held" as const };
      return t;
    }),
  };
}

function longStrings(): WorkflowSnapshot {
  const s = makeWorkflows({ seed: 3 });
  return {
    ...s,
    lines: s.lines.map((l, i) => ({
      ...l,
      name: i === 0 ? "Quarterly compliance evidence collection and export" : l.name,
      stations: l.stations.map((st, j) => ({
        ...st,
        name: j % 2 ? `${st.name} against every regional tenant database` : st.name,
      })),
    })),
    trains: s.trains.map((t) => ({
      ...t,
      label: `${t.label} release-candidate-2026-09-28-hotfix`,
    })),
  };
}

const meta: Meta<typeof Shell> = {
  title: "Viz/Workflow/WorkflowTransit",
  component: Shell,
  args: { motion: "full", flowParticles: true, onSelectRun: () => {}, onSelectStation: () => {} },
};
export default meta;

type Story = StoryObj<typeof Shell>;

export const Live: Story = {
  render: (args) => <Simulated {...args} initial={makeWorkflows()} />,
};

export const Quiet: Story = { args: { snapshot: quiet() } };

export const Incident: Story = { args: { snapshot: incident() } };

export const Large: Story = {
  render: (args) => (
    <Simulated {...args} initial={makeWorkflows({ lines: 8, trainsPerLine: 6, seed: 21 })} />
  ),
};

export const Reduced: Story = { args: { snapshot: incident(), motion: "reduced" } };

export const NoParticles: Story = { args: { snapshot: makeWorkflows(), flowParticles: false } };

export const LongStrings: Story = { args: { snapshot: longStrings() } };
