import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { makeFleet, stepFleet, type FleetSnapshot } from "../core/fleet-model";
import { mulberry32 } from "../core/semantics";
import { useSimulation } from "../core/use-simulation";
import { VizLegend } from "../core/VizLegend";

import { FLEET_HIVE_LEGEND, FleetHive } from "./FleetHive";

const meta: Meta<typeof FleetHive> = {
  title: "Viz/Fleet/FleetHive",
  component: FleetHive,
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj<typeof FleetHive>;

/** Advance a fleet a few steps so it carries recent events. */
function warm(snapshot: FleetSnapshot, steps = 3, seed = 3): FleetSnapshot {
  const rng = mulberry32(seed);
  let s = snapshot;
  for (let i = 0; i < steps; i++) s = stepFleet(s, rng, 1200);
  return s;
}

function Hive({
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
      <FleetHive
        snapshot={snapshot}
        motion={motion}
        selectedAgentId={selected}
        onSelectAgent={setSelected}
      />
      <VizLegend items={FLEET_HIVE_LEGEND} motion={motion} />
    </div>
  );
}

export const Live: Story = {
  render: () => <Hive initial={makeFleet({ clusters: 4, agentsPerCluster: 12 })} />,
};

export const Quiet: Story = {
  render: () => (
    <Hive initial={makeFleet({ clusters: 3, agentsPerCluster: 8, idle: true })} live={false} />
  ),
};

export const Incident: Story = {
  render: () => {
    const fleet = warm(makeFleet({ clusters: 4, agentsPerCluster: 12, failing: 0.3, seed: 5 }));
    const clusters = fleet.clusters.map((c, i) =>
      i === 1 ? { ...c, health: "offline" as const } : c
    );
    return <Hive initial={{ ...fleet, clusters }} live={false} />;
  },
};

export const Large: Story = {
  render: () => <Hive initial={makeFleet({ clusters: 8, agentsPerCluster: 60, seed: 9 })} />,
};

export const Reduced: Story = {
  render: () => (
    <Hive
      initial={warm(makeFleet({ clusters: 4, agentsPerCluster: 12, failing: 0.08 }))}
      motion="reduced"
    />
  ),
};

export const LongStrings: Story = {
  render: () => {
    const fleet = warm(makeFleet({ clusters: 3, agentsPerCluster: 6 }));
    return (
      <Hive
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
