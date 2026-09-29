import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { makeApp, stepApp, type AppSnapshot } from "../../core/app-model";
import { useSimulation } from "../../core/use-simulation";
import { VizLegend } from "../../core/VizLegend";

import { incidentApp, largeApp, longStringsApp, quietApp } from "./layout-fixtures";
import { SERVICE_DATA_LAYOUT_LEGEND, ServiceDataLayout } from "./ServiceDataLayout";

const TOPOLOGY = "service-data" as const;

function DiagramPlaceholder() {
  return (
    <div className="text-muted-foreground flex h-full min-h-24 items-center justify-center border border-dashed font-mono text-xs">
      diagram slot (AppIsometric or AppGraph)
    </div>
  );
}

function Shell({
  snapshot,
  motion = "full",
}: {
  snapshot: AppSnapshot;
  motion?: "full" | "reduced";
}) {
  const [selected, setSelected] = React.useState<string | null>(null);
  return (
    <div className="bg-card space-y-3 rounded-md border p-4">
      <ServiceDataLayout
        snapshot={snapshot}
        motion={motion}
        diagram={<DiagramPlaceholder />}
        onSelectNode={setSelected}
      />
      <VizLegend items={SERVICE_DATA_LAYOUT_LEGEND} motion={motion} />
      <p className="text-muted-foreground font-mono text-xs">selected: {selected ?? "none"}</p>
    </div>
  );
}

function LiveDemo({ initial }: { initial: () => AppSnapshot }) {
  const [start] = React.useState(initial);
  const snapshot = useSimulation(start, stepApp, { intervalMs: 1200, seed: 7 });
  return <Shell snapshot={snapshot} />;
}

const live = () => makeApp(TOPOLOGY);
const large = () => largeApp();

const meta: Meta<typeof ServiceDataLayout> = {
  title: "Viz/App/Layouts/ServiceDataLayout",
  component: ServiceDataLayout,
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj<typeof ServiceDataLayout>;

export const Live: Story = { render: () => <LiveDemo initial={live} /> };
export const Quiet: Story = { render: () => <Shell snapshot={quietApp(TOPOLOGY)} /> };
export const Incident: Story = { render: () => <Shell snapshot={incidentApp(TOPOLOGY)} /> };
export const Large: Story = { render: () => <LiveDemo initial={large} /> };
export const Reduced: Story = {
  render: () => <Shell snapshot={incidentApp(TOPOLOGY)} motion="reduced" />,
};
export const LongStrings: Story = { render: () => <Shell snapshot={longStringsApp(TOPOLOGY)} /> };
export const Narrow: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Shell snapshot={incidentApp(TOPOLOGY)} />
    </div>
  ),
};
