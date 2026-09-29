import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { makeApp, stepApp, type AppSnapshot } from "../../core/app-model";
import { mulberry32 } from "../../core/semantics";
import { useSimulation } from "../../core/use-simulation";
import { VizLegend } from "../../core/VizLegend";

import { SERVICE_AGENT_LAYOUT_LEGEND, ServiceAgentLayout } from "./ServiceAgentLayout";

function warm(s: AppSnapshot, steps: number, seed = 11): AppSnapshot {
  const rng = mulberry32(seed);
  for (let i = 0; i < steps; i++) s = stepApp(s, rng, 1200);
  return s;
}

function quiet(s: AppSnapshot): AppSnapshot {
  return {
    ...s,
    nodes: s.nodes.map((n) => ({
      ...n,
      load: 0,
      health: "idle",
      rps: n.rps === undefined ? undefined : 0,
    })),
    edges: s.edges.map((e) => ({ ...e, rps: 0, errorRate: 0 })),
    events: [],
  };
}

function Diagram() {
  return (
    <div className="text-muted-foreground flex h-32 items-center justify-center text-xs">
      Diagram slot (isometric or graph view)
    </div>
  );
}

function Demo({
  initial,
  live = false,
  motion = "full",
}: {
  initial: AppSnapshot;
  live?: boolean;
  motion?: "full" | "reduced";
}) {
  const snapshot = useSimulation(initial, stepApp, { paused: !live, seed: 5 });
  const [selected, setSelected] = React.useState<string>();
  return (
    <div className="bg-card rounded-md border">
      <ServiceAgentLayout
        snapshot={snapshot}
        motion={motion}
        onSelectNode={setSelected}
        diagram={<Diagram />}
      />
      <div className="text-muted-foreground border-t px-4 py-2 text-xs">
        Selected: {selected ?? "none"}
      </div>
      <div className="border-t px-4 py-2">
        <VizLegend items={SERVICE_AGENT_LAYOUT_LEGEND} motion={motion} />
      </div>
    </div>
  );
}

const meta: Meta<typeof Demo> = { title: "Viz/App/Layouts/ServiceAgentLayout", component: Demo };
export default meta;

type Story = StoryObj<typeof Demo>;

export const Live: Story = { args: { initial: warm(makeApp("service-agent"), 8), live: true } };

export const Quiet: Story = { args: { initial: quiet(makeApp("service-agent")) } };

export const Incident: Story = {
  args: { initial: warm(makeApp("service-agent", { incident: true }), 8), live: true },
};

const micro = makeApp("microservices", { seed: 7 });
export const Large: Story = {
  args: {
    initial: warm(
      {
        ...micro,
        topology: "service-agent",
        app: { slug: "shop", name: "shop" },
        nodes: [
          ...micro.nodes,
          {
            id: "agent",
            name: "fraud-agent",
            role: "agent",
            kind: "agent",
            health: "ok",
            load: 0.5,
            rps: 40,
          },
          {
            id: "agent2",
            name: "support-agent",
            role: "agent",
            kind: "agent",
            health: "ok",
            load: 0.3,
            rps: 12,
          },
          {
            id: "llm",
            name: "Model gateway",
            role: "external",
            kind: "external",
            health: "ok",
            load: 0.4,
          },
        ],
        edges: [
          ...micro.edges,
          { from: "orders", to: "agent", rps: 60, errorRate: 0 },
          { from: "agent", to: "db", rps: 30, errorRate: 0 },
          { from: "agent", to: "orders", rps: 12, errorRate: 0 },
          { from: "agent", to: "llm", rps: 20, errorRate: 0.01 },
          { from: "agent2", to: "users", rps: 8, errorRate: 0 },
          { from: "agent2", to: "llm", rps: 14, errorRate: 0 },
        ],
      },
      10
    ),
    live: true,
  },
};

export const Reduced: Story = {
  args: { initial: warm(makeApp("service-agent", { incident: true }), 8), motion: "reduced" },
};

const long = warm(makeApp("service-agent"), 6);
export const LongStrings: Story = {
  args: {
    initial: {
      ...long,
      app: { ...long.app, name: "customer-support-portal-with-an-extremely-long-application-name" },
      nodes: long.nodes.map((n) => ({
        ...n,
        name: `${n.name}-with-an-unreasonably-long-descriptive-name-for-truncation`,
      })),
    },
  },
};
