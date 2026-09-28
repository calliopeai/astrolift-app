import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  LONG_APP,
  LONG_NODES,
  TOPOLOGY,
} from "./app-observability-shell-topology-commands.fixtures";
import { TopologyScreen } from "./TopologyScreen";

/** The overview's topology panel: AppView over the app's real nodes. */
const meta: Meta = {
  title: "Screens/Apps/Tools/TopologyScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

/** Web and worker on one database; the worker failed. */
export const Full: Story = {
  render: () => <TopologyScreen {...TOPOLOGY} />,
};

export const Loading: Story = {
  render: () => <TopologyScreen {...TOPOLOGY} app={null} loading />,
};

/** The app resolved; its workloads are still loading. */
export const WorkloadsLoading: Story = {
  render: () => <TopologyScreen {...TOPOLOGY} workloadsLoading />,
};

/** No workloads yet: nothing to draw, and the way to add one. */
export const Empty: Story = {
  render: () => <TopologyScreen {...TOPOLOGY} nodes={[]} edges={[]} />,
};

/**
 * The panel has no query-error state of its own: the frame above it owns a
 * failed app query, and a failed workloads query reads as no workloads.
 */
export const NoApp: Story = {
  render: () => <TopologyScreen {...TOPOLOGY} slug="no-such-app" app={null} />,
};

export const LongStrings: Story = {
  render: () => (
    <TopologyScreen
      {...TOPOLOGY}
      slug={LONG_APP.slug}
      app={LONG_APP}
      nodes={LONG_NODES}
      edges={[]}
    />
  ),
};

export const At768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <TopologyScreen {...TOPOLOGY} />
    </div>
  ),
};
