import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  LONG_PREVIEW,
  PREVIEWS,
  previewDetailProps,
} from "../pipelines/pipelines-previews.fixtures";

import { PreviewDetailScreen } from "./PreviewDetail";

const meta: Meta = {
  title: "Screens/Previews/PreviewDetail",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

export const Full: Story = { render: () => <PreviewDetailScreen {...previewDetailProps()} /> };

export const Loading: Story = {
  render: () => <PreviewDetailScreen {...previewDetailProps({ preview: null, loading: true })} />,
};

/**
 * No preview with this id in the list window. The screen has no separate
 * empty or error state: a failed fetch also resolves to this not-found view.
 */
export const NotFound: Story = {
  render: () => <PreviewDetailScreen {...previewDetailProps({ preview: null })} />,
};

/** Approximate cost with the driver's caveats. */
export const ApproximateCost: Story = {
  render: () => <PreviewDetailScreen {...previewDetailProps({ preview: PREVIEWS[3] })} />,
};

/** Torn down: no PR link, no source, no cost. */
export const TornDown: Story = {
  render: () => <PreviewDetailScreen {...previewDetailProps({ preview: PREVIEWS[2] })} />,
};

export const LongStrings: Story = {
  render: () => <PreviewDetailScreen {...previewDetailProps({ preview: LONG_PREVIEW })} />,
};
