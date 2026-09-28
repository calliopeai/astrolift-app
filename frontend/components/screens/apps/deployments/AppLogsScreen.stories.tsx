import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { LOGS, LOGS_LONG } from "./app-deployments-logs.fixtures";
import { AppLogsScreen } from "./AppLogsScreen";

const meta: Meta = {
  title: "Screens/Apps/Deployments/AppLogsScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

/** Streaming a pod's log. */
export const Full: Story = { render: () => <AppLogsScreen {...LOGS} /> };

export const Paused: Story = {
  render: () => <AppLogsScreen {...LOGS} streaming={false} lines={[]} />,
};

export const Loading: Story = {
  render: () => <AppLogsScreen {...LOGS} app={null} loading />,
};

/** The app is loaded; its pods are still on the way. */
export const PodsLoading: Story = {
  render: () => (
    <AppLogsScreen
      {...LOGS}
      podRows={[]}
      selectedPod={null}
      podsLoading
      streaming={false}
      lines={[]}
    />
  ),
};

/** Nothing running to tail. */
export const Empty: Story = {
  render: () => (
    <AppLogsScreen {...LOGS} podRows={[]} selectedPod={null} noPods streaming={false} lines={[]} />
  ),
};

/**
 * The screen has no error state: a failed subscription leaves the waiting
 * hint, a failed app query lands on not found. Not found is the closest.
 */
export const NotFound: Story = {
  render: () => <AppLogsScreen {...LOGS} slug="no-such-app" app={null} />,
};

export const LongStrings: Story = { render: () => <AppLogsScreen {...LOGS_LONG} /> };
