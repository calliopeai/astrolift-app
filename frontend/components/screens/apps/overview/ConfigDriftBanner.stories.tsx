import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { CONFIG_DRIFT, DRIFT, LONG } from "./app-overview-banners.fixtures";
import { ConfigDriftBannerView } from "./ConfigDriftBanner";

const meta: Meta = { title: "Screens/Apps/Overview/ConfigDriftBanner" };
export default meta;

type Story = StoryObj;

export const Full: Story = {
  render: () => <ConfigDriftBannerView {...CONFIG_DRIFT} />,
};

/** Resync in flight. */
export const Loading: Story = {
  render: () => <ConfigDriftBannerView {...CONFIG_DRIFT} resyncing />,
};

/** No drift: the banner renders nothing. */
export const Empty: Story = {
  render: () => (
    <div data-testid="slot">
      <ConfigDriftBannerView {...CONFIG_DRIFT} drift={{ ...DRIFT, hasDrift: false, fields: [] }} />
    </div>
  ),
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByTestId("slot")).toBeEmptyDOMElement();
  },
};

/** No environment name, and a field path this frontend does not know. */
export const ErrorState: Story = {
  render: () => (
    <ConfigDriftBannerView
      {...CONFIG_DRIFT}
      drift={{ ...DRIFT, environmentName: "", fields: ["image_tag", "replica_count"] }}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <ConfigDriftBannerView
      {...CONFIG_DRIFT}
      drift={{ ...DRIFT, environmentName: LONG, fields: ["manifest_hash", LONG] }}
    />
  ),
};
