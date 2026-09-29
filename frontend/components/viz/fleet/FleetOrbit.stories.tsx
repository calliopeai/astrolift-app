import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { makeFleet, stepFleet, type FleetSnapshot } from "../core/fleet-model";
import { mulberry32 } from "../core/semantics";
import { useSimulation } from "../core/use-simulation";
import { VizLegend } from "../core/VizLegend";

import { FLEET_ORBIT_LEGEND, FleetOrbit } from "./FleetOrbit";

const meta: Meta<typeof FleetOrbit> = {
  title: "Viz/Fleet/FleetOrbit",
  component: FleetOrbit,
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj<typeof FleetOrbit>;

/** Advance a fleet a few steps so it carries recent launches. */
function warm(snapshot: FleetSnapshot, steps = 3, seed = 3): FleetSnapshot {
  const rng = mulberry32(seed);
  let s = snapshot;
  for (let i = 0; i < steps; i++) s = stepFleet(s, rng, 1200);
  return s;
}

function Orbit({
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
      <FleetOrbit
        snapshot={snapshot}
        motion={motion}
        selectedAgentId={selected}
        onSelectAgent={setSelected}
      />
      <VizLegend items={FLEET_ORBIT_LEGEND} motion={motion} />
    </div>
  );
}

export const Live: Story = {
  render: () => <Orbit initial={makeFleet({ clusters: 3, agentsPerCluster: 8 })} />,
};

export const Quiet: Story = {
  render: () => (
    <Orbit initial={makeFleet({ clusters: 3, agentsPerCluster: 6, idle: true })} live={false} />
  ),
};

export const Incident: Story = {
  render: () => {
    const fleet = warm(makeFleet({ clusters: 3, agentsPerCluster: 10, failing: 0.3, seed: 5 }));
    fleet.clusters[2] = { ...fleet.clusters[2], health: "offline" };
    return <Orbit initial={fleet} live={false} />;
  },
};

export const Large: Story = {
  render: () => <Orbit initial={makeFleet({ clusters: 8, agentsPerCluster: 30, seed: 9 })} />,
};

export const Reduced: Story = {
  render: () => (
    <Orbit
      initial={warm(makeFleet({ clusters: 3, agentsPerCluster: 8, failing: 0.12 }))}
      motion="reduced"
    />
  ),
};

export const LongStrings: Story = {
  render: () => {
    const fleet = warm(makeFleet({ clusters: 3, agentsPerCluster: 6 }));
    return (
      <Orbit
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
