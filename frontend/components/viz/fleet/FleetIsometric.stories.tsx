import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { makeFleet, stepFleet, type FleetSnapshot } from "../core/fleet-model";
import { mulberry32 } from "../core/semantics";
import { useSimulation } from "../core/use-simulation";
import { VizLegend } from "../core/VizLegend";

import { FLEET_ISOMETRIC_LEGEND, FleetIsometric } from "./FleetIsometric";

const meta: Meta<typeof FleetIsometric> = {
  title: "Viz/Fleet/FleetIsometric",
  component: FleetIsometric,
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj<typeof FleetIsometric>;

/** Advance a fleet a few steps so it carries recent events. */
function warm(snapshot: FleetSnapshot, steps = 3, seed = 3): FleetSnapshot {
  const rng = mulberry32(seed);
  let s = snapshot;
  for (let i = 0; i < steps; i++) s = stepFleet(s, rng, 1200);
  return s;
}

function Iso({
  initial,
  motion = "full",
  live = true,
}: {
  initial: FleetSnapshot;
  motion?: "full" | "reduced";
  live?: boolean;
}) {
  const snapshot = useSimulation(initial, stepFleet, { paused: !live, seed: 11 });
  const [selected, setSelected] = React.useState<string>();
  return (
    <div className="bg-card space-y-3 rounded-md border p-4">
      <FleetIsometric
        snapshot={snapshot}
        motion={motion}
        selectedAgentId={selected}
        onSelectAgent={setSelected}
      />
      <VizLegend items={FLEET_ISOMETRIC_LEGEND} motion={motion} />
    </div>
  );
}

export const Live: Story = {
  render: () => <Iso initial={makeFleet({ clusters: 4, agentsPerCluster: 12 })} />,
};

export const Quiet: Story = {
  render: () => (
    <Iso initial={makeFleet({ clusters: 3, agentsPerCluster: 8, idle: true })} live={false} />
  ),
};

export const Incident: Story = {
  render: () => {
    const fleet = warm(makeFleet({ clusters: 4, agentsPerCluster: 12, failing: 0.3, seed: 5 }));
    const clusters = fleet.clusters.map((c, i) =>
      i === 1 ? { ...c, health: "offline" as const } : c
    );
    return <Iso initial={{ ...fleet, clusters }} live={false} />;
  },
};

export const Large: Story = {
  render: () => <Iso initial={makeFleet({ clusters: 6, agentsPerCluster: 36, seed: 9 })} />,
};

export const Reduced: Story = {
  render: () => (
    <Iso
      initial={warm(makeFleet({ clusters: 4, agentsPerCluster: 12, failing: 0.08 }))}
      motion="reduced"
    />
  ),
};

export const LongStrings: Story = {
  render: () => {
    const fleet = warm(makeFleet({ clusters: 3, agentsPerCluster: 6 }));
    return (
      <Iso
        live={false}
        initial={{
          ...fleet,
          clusters: fleet.clusters.map((c) => ({
            ...c,
            name: `${c.name}-customer-dedicated-eks-cluster-with-a-very-long-name`,
          })),
          agents: fleet.agents.map((a) => ({
            ...a,
            name: `${a.name}-agent-that-handles-an-extremely-specific-workflow-for-finance`,
          })),
        }}
      />
    );
  },
};
