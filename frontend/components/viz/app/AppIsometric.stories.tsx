import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { TOPOLOGY_META, type TopologyKind } from "@/lib/topology";

import { makeApp, stepApp, type AppSnapshot } from "../core/app-model";
import { useSimulation } from "../core/use-simulation";
import { VizLegend } from "../core/VizLegend";

import {
  incidentApp,
  incidentMixed,
  longStrings,
  quietApp,
  twentyNodes,
} from "./app-story-fixtures";
import { APP_ISOMETRIC_LEGEND, AppIsometric } from "./AppIsometric";

function Shell({
  snapshot,
  motion = "full",
  flowParticles = true,
}: {
  snapshot: AppSnapshot;
  motion?: "full" | "reduced";
  flowParticles?: boolean;
}) {
  return (
    <div className="bg-card space-y-3 rounded-md border p-4">
      <AppIsometric snapshot={snapshot} motion={motion} flowParticles={flowParticles} />
      <VizLegend items={APP_ISOMETRIC_LEGEND} motion={motion} />
    </div>
  );
}

function LiveDemo({ initial }: { initial: () => AppSnapshot }) {
  const start = React.useMemo(() => initial(), [initial]);
  const snapshot = useSimulation(start, stepApp, { intervalMs: 1200, seed: 11 });
  return <Shell snapshot={snapshot} />;
}

const serviceAgent = () => makeApp("service-agent");
const functions = () => makeApp("functions");
const microservices = () => makeApp("microservices", { name: "shop" });

const TOPOLOGIES = Object.keys(TOPOLOGY_META) as TopologyKind[];

const meta: Meta<typeof AppIsometric> = {
  title: "Viz/App/AppIsometric",
  component: AppIsometric,
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj<typeof AppIsometric>;

export const Live: Story = { render: () => <LiveDemo initial={serviceAgent} /> };
export const LiveFunctions: Story = { render: () => <LiveDemo initial={functions} /> };
export const Quiet: Story = { render: () => <Shell snapshot={quietApp()} /> };
export const Incident: Story = { render: () => <Shell snapshot={incidentApp()} /> };
export const IncidentWithAgent: Story = { render: () => <Shell snapshot={incidentMixed()} /> };
export const Large: Story = { render: () => <LiveDemo initial={microservices} /> };
export const TwentyNodes: Story = { render: () => <LiveDemo initial={twentyNodes} /> };
export const Reduced: Story = {
  render: () => <Shell snapshot={incidentMixed()} motion="reduced" />,
};
export const NoParticles: Story = {
  render: () => <Shell snapshot={microservices()} flowParticles={false} />,
};
export const LongStrings: Story = { render: () => <Shell snapshot={longStrings()} /> };
export const Narrow: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Shell snapshot={incidentApp()} />
    </div>
  ),
};
export const EveryTopology: Story = {
  render: () => (
    <div className="grid gap-4 md:grid-cols-2">
      {TOPOLOGIES.map((t) => (
        <div key={t} className="bg-card rounded-md border p-3">
          <p className="mb-2 text-xs font-semibold">
            {TOPOLOGY_META[t].label}
            <span className="text-muted-foreground ml-2 font-normal">{TOPOLOGY_META[t].blurb}</span>
          </p>
          <AppIsometric snapshot={makeApp(t)} motion="full" />
        </div>
      ))}
    </div>
  ),
};
