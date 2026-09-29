import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { CLOUD_PROVIDERS, LONG_PLUGIN } from "../models/models-providers.fixtures";

import { CloudProvidersPanelView } from "./CloudProvidersPanel";

const meta: Meta<typeof CloudProvidersPanelView> = {
  title: "Screens/Providers/CloudProvidersPanel",
  component: CloudProvidersPanelView,
  args: CLOUD_PROVIDERS,
};
export default meta;

type Story = StoryObj<typeof CloudProvidersPanelView>;

export const Full: Story = {};

export const ListView: Story = { args: { viewMode: "list" } };

/** The available catalog disclosed under the configured providers. */
export const AvailableShown: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole("button", { name: /Show 2 available providers/ }));
    await expect(canvas.getByText("Google Cloud")).toBeInTheDocument();
  },
};

export const Loading: Story = { args: { loading: true } };

/** No provider backs a cluster yet; the catalog is still one click away. */
export const Empty: Story = { args: { configured: [] } };

/**
 * The panel has no error state: a failed query renders like an empty catalog.
 * This is the closest real state: nothing loaded at all.
 */
export const NothingLoaded: Story = { args: { pluginCount: 0, configured: [], available: [] } };

export const LongStrings: Story = {
  args: {
    configured: [{ plugin: LONG_PLUGIN, clusters: 1234 }],
    available: [LONG_PLUGIN],
  },
};

export const LongStringsList: Story = {
  args: { ...LongStrings.args, viewMode: "list" },
};
