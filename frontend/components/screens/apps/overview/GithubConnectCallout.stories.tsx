import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import {
  GITHUB_CONNECT,
  LONG,
  OAUTH_APP_CONNECTION,
  PERSONAL_CONNECTION,
} from "./app-overview-banners.fixtures";
import { GithubConnectCalloutView } from "./GithubConnectCallout";

const meta: Meta = { title: "Screens/Apps/Overview/GithubConnectCallout" };
export default meta;

type Story = StoryObj;

/** An org OAuth app exists: the CTA starts the per-user OAuth flow. */
export const Full: Story = {
  render: () => <GithubConnectCalloutView {...GITHUB_CONNECT} />,
};

/** Connections still loading: the callout renders nothing. */
export const Loading: Story = {
  render: () => (
    <div data-testid="slot">
      <GithubConnectCalloutView {...GITHUB_CONNECT} loading />
    </div>
  ),
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByTestId("slot")).toBeEmptyDOMElement();
  },
};

/** The viewer already has a personal connection: nothing to prompt. */
export const Empty: Story = {
  render: () => (
    <div data-testid="slot">
      <GithubConnectCalloutView
        {...GITHUB_CONNECT}
        connections={[OAUTH_APP_CONNECTION, PERSONAL_CONNECTION]}
      />
    </div>
  ),
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByTestId("slot")).toBeEmptyDOMElement();
  },
};

/** No OAuth app configured: the CTA falls back to source-provider settings. */
export const ErrorState: Story = {
  render: () => <GithubConnectCalloutView {...GITHUB_CONNECT} connections={[]} />,
};

/** The copy is fixed; a long connection id only lands in the href. */
export const LongStrings: Story = {
  render: () => (
    <GithubConnectCalloutView
      {...GITHUB_CONNECT}
      connections={[{ ...OAUTH_APP_CONNECTION, id: LONG }]}
    />
  ),
};
