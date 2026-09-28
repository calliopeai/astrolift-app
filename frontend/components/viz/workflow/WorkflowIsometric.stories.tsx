import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { useSimulation } from "../core/use-simulation";
import { VizLegend } from "../core/VizLegend";
import { makeWorkflows, stepWorkflows, type WorkflowSnapshot } from "../core/workflow-model";

import { WORKFLOW_ISOMETRIC_LEGEND, WorkflowIsometric } from "./WorkflowIsometric";

type Scenario = "live" | "quiet" | "incident" | "large" | "long";

function scenario(kind: Scenario): WorkflowSnapshot {
  switch (kind) {
    case "quiet": {
      const s = makeWorkflows({ trainsPerLine: 0 });
      return { ...s, segments: s.segments.map((x) => ({ ...x, rate: 0, backlog: 0 })) };
    }
    case "incident": {
      const s = makeWorkflows({ backedUp: true, trainsPerLine: 4, seed: 5 });
      return {
        ...s,
        trains: s.trains.map((t, i) =>
          i % 4 === 1 && s.lines.find((l) => l.id === t.lineId)?.stations[t.at]?.kind !== "gate"
            ? { ...t, state: "failed" as const, progress: 0 }
            : t
        ),
      };
    }
    case "large":
      return makeWorkflows({ lines: 8, trainsPerLine: 4, seed: 21 });
    case "long": {
      const s = makeWorkflows();
      return {
        ...s,
        lines: s.lines.map((l, i) => ({
          ...l,
          name: `${l.name} for the enterprise customer onboarding programme ${i}`,
          stations: l.stations.map((st) => ({
            ...st,
            name: `${st.name} with a deliberately long station name`,
          })),
        })),
        trains: s.trains.map((t) => ({ ...t, label: `${t.label}-release-candidate-build` })),
      };
    }
    default:
      return makeWorkflows();
  }
}

function Demo({
  kind = "live",
  live = true,
  motion = "full",
  flowParticles = true,
}: {
  kind?: Scenario;
  live?: boolean;
  motion?: "full" | "reduced";
  flowParticles?: boolean;
}) {
  const snapshot = useSimulation(scenario(kind), stepWorkflows, {
    paused: !live,
    intervalMs: 1200,
    seed: 3,
  });
  return (
    <div data-motion={motion} className="bg-card space-y-3 rounded-md border p-4">
      <WorkflowIsometric
        snapshot={snapshot}
        motion={motion}
        flowParticles={flowParticles}
        onSelectRun={() => {}}
        onSelectStation={() => {}}
      />
      <VizLegend items={WORKFLOW_ISOMETRIC_LEGEND} motion={motion} />
    </div>
  );
}

const meta: Meta<typeof Demo> = {
  title: "Viz/Workflow/WorkflowIsometric",
  component: Demo,
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj<typeof Demo>;

export const Live: Story = { args: { kind: "live" } };
export const Quiet: Story = { args: { kind: "quiet", live: false } };
export const Incident: Story = { args: { kind: "incident", live: false } };
export const Large: Story = { args: { kind: "large" } };
export const Reduced: Story = { args: { kind: "live", motion: "reduced" } };
export const NoParticles: Story = { args: { kind: "live", flowParticles: false } };
export const LongStrings: Story = { args: { kind: "long", live: false } };
export const Tablet: Story = {
  args: { kind: "live" },
  decorators: [
    (S) => (
      <div style={{ width: 768 }}>
        <S />
      </div>
    ),
  ],
};
