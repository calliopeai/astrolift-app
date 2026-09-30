import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import {
  FLEET_MAP,
  FLEET_MAP_EMPTY,
  FLEET_MAP_ERROR,
  FLEET_MAP_LOADING,
  FLEET_MAP_LONG,
  FLEET_OVERVIEW,
  FLEET_OVERVIEW_EMPTY,
  FLEET_OVERVIEW_ERROR,
  FLEET_OVERVIEW_LOADING,
  FLEET_OVERVIEW_LONG,
} from "./fleet-functions-logs.fixtures";
import { FleetMapPanel } from "./FleetMapPanel";
import { FleetOverviewScreen } from "./FleetOverviewScreen";

const meta: Meta = {
  title: "Screens/Fleet/FleetOverviewScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

export const Full: Story = {
  render: () => <FleetOverviewScreen {...FLEET_OVERVIEW} map={<FleetMapPanel {...FLEET_MAP} />} />,
};

export const OpenMap: Story = {
  ...Full,
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole("link", { name: "Open fleet map" })
    ).toHaveAttribute("href", "/fleet/map");
  },
};

export const Loading: Story = {
  render: () => (
    <FleetOverviewScreen
      {...FLEET_OVERVIEW_LOADING}
      map={<FleetMapPanel {...FLEET_MAP_LOADING} />}
    />
  ),
};

/** A new org: no agents, runtimes or tasks yet. */
export const Empty: Story = {
  render: () => (
    <FleetOverviewScreen {...FLEET_OVERVIEW_EMPTY} map={<FleetMapPanel {...FLEET_MAP_EMPTY} />} />
  ),
};

/**
 * The overview has no page-level error; the two tables and the map show
 * their own. The stat tiles fall back to zero.
 */
export const LoadFailed: Story = {
  render: () => (
    <FleetOverviewScreen {...FLEET_OVERVIEW_ERROR} map={<FleetMapPanel {...FLEET_MAP_ERROR} />} />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <FleetOverviewScreen {...FLEET_OVERVIEW_LONG} map={<FleetMapPanel {...FLEET_MAP_LONG} />} />
  ),
};
