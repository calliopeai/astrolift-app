import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { ActivityFeed } from "@/components/ActivityFeed";

import { DashboardScreen } from "./DashboardScreen";
import {
  ACTIVITY,
  DASHBOARD,
  DASHBOARD_EMPTY,
  DASHBOARD_ERROR,
  DASHBOARD_LOADING,
  DASHBOARD_LONG,
} from "./dashboard-build-dev.fixtures";

const meta: Meta = {
  title: "Screens/Dashboard/DashboardScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

export const Full: Story = {
  render: () => <DashboardScreen {...DASHBOARD} activity={<ActivityFeed {...ACTIVITY} />} />,
};

export const Loading: Story = {
  render: () => (
    <DashboardScreen
      {...DASHBOARD_LOADING}
      activity={<ActivityFeed {...ACTIVITY} items={[]} loading hasMore={false} />}
    />
  ),
};

export const Empty: Story = {
  render: () => (
    <DashboardScreen
      {...DASHBOARD_EMPTY}
      activity={<ActivityFeed {...ACTIVITY} items={[]} hasMore={false} />}
    />
  ),
};

/** The teams list failed with a non-404 error: the banner shows above the tiles. */
export const LoadFailed: Story = {
  render: () => (
    <DashboardScreen
      {...DASHBOARD_ERROR}
      activity={<ActivityFeed {...ACTIVITY} items={[]} hasMore={false} />}
    />
  ),
};

/** A role that can read agents only: no apps, workflows, projects, billing, or audit. */
export const AgentsOnly: Story = {
  render: () => (
    <DashboardScreen
      {...DASHBOARD}
      tileIds={["agents"]}
      canViewApps={false}
      canViewWorkflows={false}
      canViewProjects={false}
      canViewAudit={false}
    />
  ),
};

export const LongStrings: Story = {
  render: () => <DashboardScreen {...DASHBOARD_LONG} activity={<ActivityFeed {...ACTIVITY} />} />,
};
