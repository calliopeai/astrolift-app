import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { makeApp } from "../../core/app-model";
import { HEALTH_COLOR } from "../../core/semantics";

import { Flash, HealthDot, LoadBar, PhosphorDot } from "./layouts-b-parts";

const node = makeApp("functions").nodes[2];

function Parts({ motion }: { motion: "full" | "reduced" }) {
  return (
    <div data-motion={motion} className="grid max-w-sm gap-4">
      <div className="flex items-center gap-3">
        <HealthDot health="ok" />
        <HealthDot health="degraded" />
        <HealthDot health="failing" />
        <HealthDot health="idle" />
      </div>
      <LoadBar node={node} />
      <LoadBar node={{ ...node, load: 0.97, health: "failing" }} label="concurrency" />
      <div className="relative h-8 w-24 border">
        <Flash eventId="e1" age={400} motion={motion} color={HEALTH_COLOR.ok} />
      </div>
      <div className="relative h-4 w-24">
        <PhosphorDot age={3000} color={HEALTH_COLOR.ok} />
      </div>
    </div>
  );
}

const meta: Meta<typeof Parts> = {
  title: "Viz/App/Layouts/LayoutsBParts",
  component: Parts,
  args: { motion: "full" },
};
export default meta;

type Story = StoryObj<typeof Parts>;

export const Default: Story = {};
export const Reduced: Story = { args: { motion: "reduced" } };
