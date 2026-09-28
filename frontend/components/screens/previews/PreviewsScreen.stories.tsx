import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { LONG_PREVIEW, previewsProps } from "../pipelines/pipelines-previews.fixtures";

import { PreviewsScreen } from "./PreviewsScreen";

const meta: Meta = {
  title: "Screens/Previews/PreviewsScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

export const Full: Story = { render: () => <PreviewsScreen {...previewsProps()} /> };

export const Loading: Story = {
  render: () => (
    <PreviewsScreen {...previewsProps({ state: "loading", rows: [], totalCount: null })} />
  ),
};

export const Empty: Story = {
  render: () => <PreviewsScreen {...previewsProps({ state: "empty", rows: [], totalCount: 0 })} />,
};

export const EmptyFiltered: Story = {
  render: () => (
    <PreviewsScreen
      {...previewsProps({ state: "emptyFiltered", rows: [], isFiltered: true, search: "zzz" })}
    />
  ),
};

export const ErrorState: Story = {
  render: () => (
    <PreviewsScreen
      {...previewsProps({
        state: "error",
        rows: [],
        error: new globalThis.Error("upstream timed out"),
      })}
    />
  ),
};

/** Without app.deploy there is no Tear down action. */
export const ReadOnly: Story = {
  render: () => <PreviewsScreen {...previewsProps({}, { canTearDown: false })} />,
};

/** A teardown in flight: every Tear down button is disabled. */
export const TearingDown: Story = {
  render: () => <PreviewsScreen {...previewsProps({}, { tearingDown: true })} />,
};

export const LongStrings: Story = {
  render: () => <PreviewsScreen {...previewsProps({ rows: [LONG_PREVIEW], totalCount: 1 })} />,
};
