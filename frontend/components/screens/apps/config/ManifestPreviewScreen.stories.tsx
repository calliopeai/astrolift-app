import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import {
  LONG,
  LONG_RENDERED,
  PREVIEW_PROPS,
  RENDER_ERROR,
  RENDERED,
} from "./app-config-agent.fixtures";
import { ManifestPreviewScreen } from "./ManifestPreviewScreen";

const meta: Meta<typeof ManifestPreviewScreen> = {
  title: "Screens/Apps/Config/ManifestPreviewScreen",
  component: ManifestPreviewScreen,
  args: PREVIEW_PROPS,
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj<typeof ManifestPreviewScreen>;

export const Full: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("3 resources")).toBeInTheDocument();
  },
};

export const Loading: Story = { args: { loading: true, result: null } };

/** No manifest came back: the screen reads it as app not found. */
export const NotFound: Story = { args: { result: null } };

/** Rendered, but no resources came out. */
export const Empty: Story = { args: { result: { ...RENDERED, resources: [] } } };

export const RenderFailed: Story = {
  args: { result: RENDER_ERROR },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("Manifest could not be rendered")).toBeInTheDocument();
  },
};

export const LongStrings: Story = { args: { slug: LONG, result: LONG_RENDERED } };

export const QueryFailed: Story = {
  args: {
    result: null,
    error: { name: "Error", message: "Permission denied while loading this section" },
  },
};
export const EnvironmentsFailed: Story = {
  args: {
    environments: [],
    environmentsError: { name: "Error", message: "Permission denied while loading this section" },
  },
};
