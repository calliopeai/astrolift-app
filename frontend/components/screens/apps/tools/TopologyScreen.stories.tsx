import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  LONG_APP,
  LONG_NODES,
  TOPOLOGY,
} from "./app-observability-shell-topology-commands.fixtures";
import { TopologyScreen } from "./TopologyScreen";

const meta: Meta = {
  title: "Screens/Apps/Tools/TopologyScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

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

/** No workloads yet: nothing to draw. */
export const Empty: Story = {
  render: () => <TopologyScreen {...TOPOLOGY} nodes={[]} edges={[]} />,
};

/**
 * The screen has no query-error state; an unknown slug (or no permission)
 * is the closest real one.
 */
export const NotFound: Story = {
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
