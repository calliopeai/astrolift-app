import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { makeApp } from "../../core/app-model";

import { FlowLine, Kpi, NodeButton, ReplicaPips, StatusDot } from "./layout-parts";

const node = makeApp("service").nodes[1];

function Parts({ motion }: { motion: "full" | "reduced" }) {
  return (
    <div data-motion={motion} className="grid max-w-xl gap-4">
      <div className="flex items-center gap-3">
        <StatusDot health="ok" />
        <StatusDot health="degraded" />
        <StatusDot health="failing" />
        <StatusDot health="idle" />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Kpi label="Requests/s" value="240" health="ok" series={[120, 180, 160, 240, 210, 240]} />
        <Kpi label="Error rate" value="22%" health="failing" series={[0.01, 0.02, 0.2, 0.22]} />
      </div>
      <ReplicaPips ready={1} desired={3} health="degraded" />
      <FlowLine rps={200} share={0.8} health="ok" />
      <FlowLine rps={20} share={0.1} health="failing" />
      <FlowLine rps={0} share={0} health="idle" />
      <NodeButton node={node} health="ok" />
    </div>
  );
}

const meta: Meta<typeof Parts> = {
  title: "Viz/App/Layouts/Parts",
  component: Parts,
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj<typeof Parts>;

export const Full: Story = { render: () => <Parts motion="full" /> };
export const Reduced: Story = { render: () => <Parts motion="reduced" /> };
