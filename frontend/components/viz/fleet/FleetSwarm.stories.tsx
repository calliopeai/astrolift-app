import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { makeFleet, stepFleet, type FleetSnapshot } from "../core/fleet-model";
import { useSimulation } from "../core/use-simulation";
import { VizLegend } from "../core/VizLegend";

import { FLEET_SWARM_LEGEND, FleetSwarm } from "./FleetSwarm";

/**
 * Clusters as hives with their agents swarming around them on trailing
 * tendrils. Swarm speed is load, tendril thickness is active runs, a failing
 * agent drifts out with a frayed tendril, and dispatches pulse hub to hive to
 * agent.
 */
function Demo({
  snapshot,
  motion = "full",
  live = false,
  width,
}: {
  snapshot: FleetSnapshot;
  motion?: "full" | "reduced";
  live?: boolean;
  width?: number;
}) {
  const state = useSimulation(snapshot, stepFleet, { paused: !live, seed: 5, intervalMs: 1000 });
  const [selected, setSelected] = React.useState<string>();
  return (
    <div className="bg-card space-y-3 rounded-md border p-3" style={width ? { width } : undefined}>
      <FleetSwarm
        snapshot={state}
        motion={motion}
        selectedAgentId={selected}
        onSelectAgent={setSelected}
      />
      <VizLegend items={FLEET_SWARM_LEGEND} motion={motion} />
    </div>
  );
}

const meta: Meta<typeof Demo> = {
  title: "Viz/Fleet/FleetSwarm",
  component: Demo,
  args: { snapshot: makeFleet({ clusters: 3, agentsPerCluster: 9 }), live: true },
};
export default meta;

type Story = StoryObj<typeof Demo>;

export const Live: Story = {};
export const Quiet: Story = {
  args: { snapshot: makeFleet({ clusters: 3, agentsPerCluster: 6, idle: true }), live: false },
};
export const Incident: Story = {
  args: { snapshot: makeFleet({ clusters: 3, agentsPerCluster: 9, failing: 0.3 }) },
};
export const Large: Story = {
  args: { snapshot: makeFleet({ clusters: 6, agentsPerCluster: 36 }) },
};
export const SingleHive: Story = {
  args: { snapshot: makeFleet({ clusters: 1, agentsPerCluster: 14 }) },
};
export const Reduced: Story = { args: { motion: "reduced", live: false } };
export const LongStrings: Story = {
  args: {
    live: false,
    snapshot: (() => {
      const s = makeFleet({ clusters: 2, agentsPerCluster: 5 });
      s.clusters[0].name = "on-prem-salt-lake-city-datacenter-rack-07-gpu-pool-canary";
      s.agents[0].name = "support-first-line-triage-and-escalation-agent-with-a-very-long-name";
      return s;
    })(),
  },
};
export const At768: Story = { args: { width: 768 } };
