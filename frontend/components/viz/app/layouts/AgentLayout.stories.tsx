import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { makeApp, stepApp, type AppNode, type AppSnapshot } from "../../core/app-model";
import { mulberry32 } from "../../core/semantics";
import { useSimulation } from "../../core/use-simulation";
import { VizLegend } from "../../core/VizLegend";

import { AGENT_LAYOUT_LEGEND, AgentLayout } from "./AgentLayout";

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
    <div className="text-muted-foreground flex h-40 items-center justify-center text-xs">
      Diagram slot
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
      <AgentLayout
        snapshot={snapshot}
        motion={motion}
        onSelectNode={setSelected}
        diagram={<Diagram />}
      />
      <div className="text-muted-foreground border-t px-4 py-2 text-xs">
        Selected: {selected ?? "none"}
      </div>
      <div className="border-t px-4 py-2">
        <VizLegend items={AGENT_LAYOUT_LEGEND} motion={motion} />
      </div>
    </div>
  );
}

const meta: Meta<typeof Demo> = { title: "Viz/App/Layouts/AgentLayout", component: Demo };
export default meta;

type Story = StoryObj<typeof Demo>;

export const Live: Story = { args: { initial: warm(makeApp("agent"), 8), live: true } };

export const Quiet: Story = { args: { initial: quiet(makeApp("agent")) } };

const incident = makeApp("agent", { incident: true });
export const Incident: Story = {
  args: {
    initial: warm(
      {
        ...incident,
        edges: incident.edges.map((e) => (e.to === "crm" ? { ...e, errorRate: 0.3 } : e)),
      },
      8
    ),
    live: true,
  },
};

const TOOLS = [
  "Model gateway",
  "HubSpot",
  "Gmail",
  "Slack",
  "Apollo",
  "Postgres",
  "S3",
  "Search API",
];
function largeAgents(): AppSnapshot {
  const base = makeApp("agent", { seed: 5 });
  const rng = mulberry32(21);
  const agents: AppNode[] = ["bdr-outreach", "demand-scout", "inbox-triage"].map((name, i) => ({
    id: `a${i}`,
    name,
    role: "agent",
    kind: "agent",
    health: "ok",
    load: 0.2 + rng() * 0.6,
    rps: Math.round(rng() * 40),
  }));
  const tools: AppNode[] = TOOLS.map((name, i) => ({
    id: `t${i}`,
    name,
    role: "external",
    kind: "external",
    health: "ok",
    load: rng() * 0.8,
  }));
  return {
    ...base,
    nodes: [base.nodes[0], ...agents, ...tools],
    edges: [
      ...agents.map((a) => ({ from: "sched", to: a.id, rps: 1, errorRate: 0 })),
      ...agents.flatMap((a, i) =>
        tools
          .filter((_, j) => (i + j) % 2 === 0 || j === 0)
          .map((t) => ({
            from: a.id,
            to: t.id,
            rps: Math.round(5 + rng() * 60),
            errorRate: rng() * 0.01,
          }))
      ),
    ],
  };
}

export const Large: Story = { args: { initial: warm(largeAgents(), 10), live: true } };

export const Reduced: Story = {
  args: { initial: warm(makeApp("agent", { incident: true }), 8), motion: "reduced" },
};

const long = warm(makeApp("agent"), 6);
export const LongStrings: Story = {
  args: {
    initial: {
      ...long,
      nodes: long.nodes.map((n) => ({
        ...n,
        name:
          n.role === "schedule"
            ? "every 15 min"
            : `${n.name}-with-an-unreasonably-long-descriptive-name`,
      })),
    },
  },
};
