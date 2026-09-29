import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  FLEET_MAP,
  FLEET_MAP_EMPTY,
  FLEET_MAP_ERROR,
  FLEET_MAP_LOADING,
  FLEET_MAP_LONG,
} from "./fleet-functions-logs.fixtures";
import { FleetMapPanel } from "./FleetMapPanel";

const meta: Meta = {
  title: "Screens/Fleet/FleetMapPanel",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

export const Full: Story = { render: () => <FleetMapPanel {...FLEET_MAP} /> };

/** The org has not resolved, or the first poll has not landed. */
export const Loading: Story = { render: () => <FleetMapPanel {...FLEET_MAP_LOADING} /> };

/** No tasks inside the lookback window. */
export const Empty: Story = { render: () => <FleetMapPanel {...FLEET_MAP_EMPTY} /> };

/** The transition feed failed before any task was seen. */
export const LoadFailed: Story = { render: () => <FleetMapPanel {...FLEET_MAP_ERROR} /> };

export const LongStrings: Story = { render: () => <FleetMapPanel {...FLEET_MAP_LONG} /> };
