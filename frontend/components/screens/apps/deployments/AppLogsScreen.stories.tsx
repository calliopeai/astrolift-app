import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { LOGS, LOGS_LONG } from "./app-deployments-logs.fixtures";
import { AppLogsScreen } from "./AppLogsScreen";

const meta: Meta = {
  title: "Screens/Apps/Deployments/AppLogsScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

/** Streaming a pod's log in the shared LogView: follow, level filter, download. */
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

/** The log subscription failed before a line arrived: Retry restarts it. */
export const SubscriptionError: Story = {
  render: () => (
    <AppLogsScreen
      {...LOGS}
      lines={[]}
      error={{ name: "Error", message: "Subscription closed: pod storefront-web is gone" }}
    />
  ),
};

/** A failed app query lands on not found. */
export const NotFound: Story = {
  render: () => <AppLogsScreen {...LOGS} slug="no-such-app" app={null} />,
};

/** 5000 lines: chunked rendering keeps the pane cheap without virtualising. */
export const ManyLines: Story = {
  render: () => (
    <AppLogsScreen
      {...LOGS}
      lines={Array.from({ length: 5000 }, (_, i) => ({
        ...LOGS.lines[i % LOGS.lines.length],
        message: `${LOGS.lines[i % LOGS.lines.length].message} #${i}`,
      }))}
    />
  ),
};

export const LongStrings: Story = { render: () => <AppLogsScreen {...LOGS_LONG} /> };

export const W768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <AppLogsScreen {...LOGS_LONG} />
    </div>
  ),
};
