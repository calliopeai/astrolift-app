import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { FLEET_VIEWS, type FleetView } from "@/lib/viz-prefs";

import { HEALTH_COLOR } from "./semantics";
import { VizFrame } from "./VizFrame";

function Demo({ motion = "full" as const }: { motion?: "full" | "reduced" }) {
  const [view, setView] = React.useState<FleetView>("orbit");
  return (
    <VizFrame
      title="Agent fleet"
      description="18 agents across 3 clusters"
      styles={FLEET_VIEWS}
      value={view}
      onChange={setView}
      motion={motion}
      legend={[
        { glyph: "glow", color: HEALTH_COLOR.ok, label: "Healthy" },
        { glyph: "flicker", color: HEALTH_COLOR.failing, label: "Failing" },
      ]}
    >
      <div className="text-muted-foreground grid h-64 place-items-center text-sm">
        {FLEET_VIEWS[view].label} renderer goes here
      </div>
    </VizFrame>
  );
}

const meta: Meta<typeof Demo> = { title: "Viz/Core/VizFrame", component: Demo };
export default meta;

type Story = StoryObj<typeof Demo>;

export const Default: Story = {};
export const Reduced: Story = { args: { motion: "reduced" } };
