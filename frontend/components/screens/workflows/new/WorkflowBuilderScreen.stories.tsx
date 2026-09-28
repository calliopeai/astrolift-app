import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { PatternCard, WorkflowBuilderScreen } from "./WorkflowBuilderScreen";
import { BUILDER, LONG_BUILDER_NAME } from "./workflows-new-builder.fixtures";
import { PATTERNS } from "./workflow-patterns";

const meta: Meta = {
  title: "Screens/Workflows/New/WorkflowBuilderScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

/** Blank form, default pattern: the screen loads no data, so this is also its empty state. */
export const Full: Story = { render: () => <WorkflowBuilderScreen {...BUILDER} /> };

/** Opened with `?pattern=fan_out`. */
export const PatternFromQuery: Story = {
  render: () => <WorkflowBuilderScreen {...BUILDER} initialPattern="fan_out" />,
};

/** The create mutation is in flight (the screen has no data loading of its own). */
export const Creating: Story = {
  render: () => <WorkflowBuilderScreen {...BUILDER} creating />,
};

export const NoCreateAccess: Story = {
  render: () => <WorkflowBuilderScreen {...BUILDER} canCreate={false} />,
};

/** Typing a name derives the slug until the slug is edited by hand. */
export const LongStrings: Story = {
  render: () => <WorkflowBuilderScreen {...BUILDER} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.type(canvas.getByPlaceholderText("e.g. PR Review Loop"), LONG_BUILDER_NAME);
    await expect(canvas.getByPlaceholderText("e.g. pr-review-loop")).toHaveValue(
      "quarterly-enterprise-account-research-enrichment-legal-review-and-multi-region-outbound-pipeline"
    );
  },
};

export const PatternCardSelected: Story = {
  render: () => (
    <div className="max-w-sm p-4">
      <PatternCard pattern={PATTERNS[2]} selected onSelect={() => {}} />
    </div>
  ),
};
