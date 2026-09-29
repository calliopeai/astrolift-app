import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { HEALTH_COLOR } from "./semantics";
import { VizLegend, WORKFLOW_SHAPE_LEGEND } from "./VizLegend";

const meta: Meta<typeof VizLegend> = {
  title: "Viz/Core/VizLegend",
  component: VizLegend,
  args: {
    motion: "full",
    items: [
      { glyph: "glow", color: HEALTH_COLOR.ok, label: "Glow: healthy" },
      { glyph: "dot", color: HEALTH_COLOR.degraded, label: "Degraded" },
      { glyph: "flicker", color: HEALTH_COLOR.failing, label: "Flicker: failing" },
      { glyph: "dot", color: HEALTH_COLOR.idle, label: "Dark: idle" },
      { glyph: "spark", color: HEALTH_COLOR.ok, label: "Spark: run started" },
      { glyph: "flow", label: "Dash speed: throughput" },
      { glyph: "bar", label: "Height: load" },
      { glyph: "signal", color: HEALTH_COLOR.degraded, label: "Signal: waiting on approval" },
      { glyph: "ring", label: "Ring: dispatch" },
    ],
  },
};
export default meta;

type Story = StoryObj<typeof VizLegend>;

export const AllGlyphs: Story = {};
export const Reduced: Story = { args: { motion: "reduced" } };
export const LongLabels: Story = {
  args: {
    items: [
      {
        glyph: "glow",
        color: HEALTH_COLOR.ok,
        label: "Glow: the agent answered its last heartbeat and has no failed runs in the window",
      },
      {
        glyph: "flicker",
        color: HEALTH_COLOR.failing,
        label: "Flicker: the last run failed or the agent stopped answering heartbeats",
      },
    ],
  },
};

/** The loop, round, fanout, supervisor and nested-workflow encodings. */
export const WorkflowShapes: Story = {
  args: { items: Object.values(WORKFLOW_SHAPE_LEGEND) },
};
export const WorkflowShapesReduced: Story = {
  args: { items: Object.values(WORKFLOW_SHAPE_LEGEND), motion: "reduced" },
};
