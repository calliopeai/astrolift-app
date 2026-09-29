import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { makeFleet, stepFleet, type FleetSnapshot } from "../core/fleet-model";
import { useSimulation } from "../core/use-simulation";
import { VizLegend } from "../core/VizLegend";

import { FLEET_GRAPH_LEGEND, FleetGraph } from "./FleetGraph";

/**
 * The org hub, clusters and agents as a force graph. Busy edges carry a
 * moving dash, dispatches pulse hub to cluster to agent, failing agents
 * flicker. Drag a node to pin it, double-click to release.
 */
function Demo({
  snapshot,
  motion = "full",
  live = false,
}: {
  snapshot: FleetSnapshot;
  motion?: "full" | "reduced";
  live?: boolean;
}) {
  const state = useSimulation(snapshot, stepFleet, { paused: !live, seed: 3 });
  const [selected, setSelected] = React.useState<string>();
  return (
    <div className="bg-card space-y-3 rounded-md border p-3">
      <FleetGraph
        snapshot={state}
        motion={motion}
        selectedAgentId={selected}
        onSelectAgent={setSelected}
      />
      <VizLegend items={FLEET_GRAPH_LEGEND} motion={motion} />
    </div>
  );
}

const meta: Meta<typeof Demo> = {
  title: "Viz/Fleet/FleetGraph",
  component: Demo,
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj<typeof Demo>;

export const Live: Story = { args: { snapshot: makeFleet(), live: true } };

export const Quiet: Story = { args: { snapshot: makeFleet({ idle: true }) } };

export const Incident: Story = {
  args: { snapshot: makeFleet({ failing: 0.3, seed: 11 }), live: true },
};

export const Large: Story = {
  args: { snapshot: makeFleet({ clusters: 8, agentsPerCluster: 26 }), live: true },
};

export const Reduced: Story = {
  args: { snapshot: makeFleet({ failing: 0.15 }), motion: "reduced", live: true },
};

export const LongStrings: Story = {
  args: {
    snapshot: (() => {
      const fleet = makeFleet({ clusters: 2, agentsPerCluster: 4 });
      return {
        ...fleet,
        clusters: fleet.clusters.map((c, i) => ({
          ...c,
          name: `${c.name}-production-us-east-1-primary-cluster-${i}`,
        })),
        agents: fleet.agents.map((a) => ({
          ...a,
          name: `${a.name}-with-an-extremely-long-descriptive-agent-name`,
        })),
      };
    })(),
  },
};
