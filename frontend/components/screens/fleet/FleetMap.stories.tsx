import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { CLUSTER_LIVENESS, FLEET_MAP_LONG, TRANSITIONS } from "./fleet-functions-logs.fixtures";
import { FleetMap } from "./FleetMap";

const meta: Meta = {
  title: "Screens/Fleet/FleetMap",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

/** Two dispatchers, two clusters (one degraded), agents in every state. */
export const Full: Story = {
  render: () => <FleetMap tasks={TRANSITIONS} clusterLiveness={CLUSTER_LIVENESS} height={480} />,
};

/** Nothing routed yet: the pipeline is anchored on the dispatch service. */
export const NothingRouted: Story = {
  render: () => (
    <FleetMap
      tasks={TRANSITIONS.map((t) => ({ ...t, status: "queued", dispatcher: null }))}
      clusterLiveness={new Map()}
      height={320}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <FleetMap tasks={FLEET_MAP_LONG.tasks} clusterLiveness={CLUSTER_LIVENESS} height={480} />
  ),
};
