import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { VizLegend } from "@/components/viz/core/VizLegend";

import { WORKFLOW_GLYPH_LEGEND, WorkflowGlyph } from "./WorkflowGlyph";
import { LONG_ROWS, WORKFLOW_ROWS } from "./workflows-list.fixtures";

const meta: Meta = {
  title: "Screens/Workflows/List/WorkflowGlyph",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

/** Every shape the fixtures hold, each with its name and the shape in words. */
export const Full: Story = {
  render: () => (
    <div className="flex max-w-md flex-col gap-3">
      {WORKFLOW_ROWS.map((row) => (
        <div key={row.id} className="flex min-w-0 items-center gap-3">
          <WorkflowGlyph line={row.line} />
          <span className="min-w-0 truncate text-sm">{row.name}</span>
        </div>
      ))}
      <VizLegend items={WORKFLOW_GLYPH_LEGEND} motion="full" />
    </div>
  ),
};

export const FanOut: Story = {
  render: () => (
    <WorkflowGlyph line={WORKFLOW_ROWS.find((r) => r.slug === "research-fan-out")!.line} />
  ),
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole("img", { name: "3 stages: fan-out, join" })
    ).toBeInTheDocument();
  },
};

/** A configured workflow whose definition is not visible: no stages to draw. */
export const Empty: Story = {
  render: () => <WorkflowGlyph line={{ id: "none", name: "none", stations: [] }} />,
};

/** Nine stages: the first seven, then "+2". */
export const LongStrings: Story = {
  render: () => (
    <div className="w-24">
      <WorkflowGlyph line={LONG_ROWS.find((r) => r.kind !== "configured")!.line} />
    </div>
  ),
};
