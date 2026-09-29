import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { makeFleet, stepFleet, type FleetSnapshot } from "../core/fleet-model";
import { useSimulation } from "../core/use-simulation";
import { VizLegend } from "../core/VizLegend";

import { FLEET_LIST_LEGEND, FleetList } from "./FleetList";

function List({
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
      <FleetList
        snapshot={snapshot}
        motion={motion}
        selectedAgentId={selected}
        onSelectAgent={setSelected}
      />
      <VizLegend items={FLEET_LIST_LEGEND} motion={motion} />
    </div>
  );
}

const meta: Meta<typeof List> = {
  title: "Viz/Fleet/FleetList",
  component: List,
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj<typeof List>;

export const Live: Story = { render: () => <List initial={makeFleet()} /> };

export const Quiet: Story = {
  render: () => <List initial={makeFleet({ idle: true })} live={false} />,
};

export const Incident: Story = {
  render: () => <List initial={makeFleet({ failing: 0.3, seed: 5 })} live={false} />,
};

export const Large: Story = {
  render: () => <List initial={makeFleet({ clusters: 6, agentsPerCluster: 36, seed: 9 })} />,
};

export const Reduced: Story = {
  render: () => <List initial={makeFleet({ failing: 0.15 })} motion="reduced" live={false} />,
};

export const Empty: Story = {
  render: () => <List initial={{ ...makeFleet(), agents: [] }} live={false} />,
};

export const LongStrings: Story = {
  render: () => {
    const fleet = makeFleet({ clusters: 2, agentsPerCluster: 4 });
    return (
      <List
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
