import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { makeFleet, stepFleet, type FleetSnapshot } from "../core/fleet-model";
import { mulberry32 } from "../core/semantics";
import { useSimulation } from "../core/use-simulation";
import { VizLegend } from "../core/VizLegend";

import { FLEET_HEARTBEAT_LEGEND, FleetHeartbeat } from "./FleetHeartbeat";

/** Run a fleet forward so the wall opens with a window of events on it. */
function warm(fleet: FleetSnapshot, steps: number, seed = 11): FleetSnapshot {
  const rng = mulberry32(seed);
  let f = fleet;
  for (let i = 0; i < steps; i++) f = stepFleet(f, rng, 1200);
  return f;
}

function Demo({
  initial,
  live = false,
  motion = "full",
}: {
  initial: FleetSnapshot;
  live?: boolean;
  motion?: "full" | "reduced";
}) {
  const snapshot = useSimulation(initial, stepFleet, { paused: !live, seed: 5 });
  const [selected, setSelected] = React.useState<string>();
  return (
    <div className="bg-card rounded-md border">
      <FleetHeartbeat
        snapshot={snapshot}
        motion={motion}
        selectedAgentId={selected}
        onSelectAgent={setSelected}
      />
      <div className="border-t px-4 py-2">
        <VizLegend items={FLEET_HEARTBEAT_LEGEND} motion={motion} />
      </div>
    </div>
  );
}

const meta: Meta<typeof Demo> = { title: "Viz/Fleet/FleetHeartbeat", component: Demo };
export default meta;

type Story = StoryObj<typeof Demo>;

export const Live: Story = { args: { initial: warm(makeFleet(), 8), live: true } };

export const Quiet: Story = { args: { initial: makeFleet({ idle: true }) } };

export const Incident: Story = {
  args: { initial: warm(makeFleet({ failing: 0.35, seed: 3 }), 9), live: true },
};

export const Large: Story = {
  args: { initial: warm(makeFleet({ clusters: 6, agentsPerCluster: 36 }), 8), live: true },
};

export const Reduced: Story = {
  args: { initial: warm(makeFleet({ failing: 0.15 }), 8), motion: "reduced" },
};

const long = warm(makeFleet({ clusters: 2, agentsPerCluster: 3 }), 6);
export const LongStrings: Story = {
  args: {
    initial: {
      ...long,
      clusters: long.clusters.map((c) => ({
        ...c,
        name: `${c.name}-customer-dedicated-production-cluster-with-a-very-long-name`,
      })),
      agents: long.agents.map((a) => ({
        ...a,
        name: `${a.name}-agent-with-an-unreasonably-long-descriptive-name-v2`,
      })),
    },
  },
};
