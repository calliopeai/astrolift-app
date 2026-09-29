import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import {
  makeApp,
  stepApp,
  type AppEdge,
  type AppNode,
  type AppSnapshot,
} from "../../core/app-model";
import { mulberry32 } from "../../core/semantics";
import { useSimulation } from "../../core/use-simulation";
import { VizLegend } from "../../core/VizLegend";

import { FUNCTIONS_LAYOUT_LEGEND, FunctionsLayout } from "./FunctionsLayout";

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
    <div className="text-muted-foreground flex h-24 items-center justify-center text-xs">
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
      <FunctionsLayout
        snapshot={snapshot}
        motion={motion}
        onSelectNode={setSelected}
        diagram={<Diagram />}
      />
      <div className="text-muted-foreground border-t px-4 py-2 text-xs">
        Selected: {selected ?? "none"}
      </div>
      <div className="border-t px-4 py-2">
        <VizLegend items={FUNCTIONS_LAYOUT_LEGEND} motion={motion} />
      </div>
    </div>
  );
}

const meta: Meta<typeof Demo> = { title: "Viz/App/Layouts/FunctionsLayout", component: Demo };
export default meta;

type Story = StoryObj<typeof Demo>;

export const Live: Story = { args: { initial: warm(makeApp("functions"), 8), live: true } };

export const Quiet: Story = { args: { initial: quiet(makeApp("functions")) } };

export const Incident: Story = {
  args: { initial: warm(makeApp("functions", { incident: true }), 8), live: true },
};

function largeFunctions(): AppSnapshot {
  const base = makeApp("functions", { seed: 4 });
  const rng = mulberry32(8);
  const mk = (id: string, name: string, role: AppNode["role"], kind: string): AppNode => ({
    id,
    name,
    role,
    kind,
    health: "ok",
    load: 0.1 + rng() * 0.8,
    rps: role === "data" ? undefined : Math.round(rng() * 400),
  });
  const triggers = ["HTTP", "uploads", "orders queue", "cron"].map((n, i) =>
    mk(`tr${i}`, n, "trigger", ["http", "s3", "sqs", "schedule"][i])
  );
  const fns = Array.from({ length: 16 }, (_, i) =>
    mk(`f${i}`, `fn-${i + 1}`, "function", "function")
  );
  const sinks = ["media", "events", "warehouse"].map((n, i) => mk(`s${i}`, n, "data", "s3"));
  const edges: AppEdge[] = [];
  const e = (from: string, to: string) =>
    edges.push({ from, to, rps: Math.round(10 + rng() * 300), errorRate: rng() * 0.01 });
  fns.slice(0, 10).forEach((f, i) => e(triggers[i % 4].id, f.id));
  fns.slice(10).forEach((f, i) => e(fns[i * 2].id, f.id));
  fns.slice(10).forEach((f, i) => e(f.id, sinks[i % 3].id));
  return { ...base, nodes: [...triggers, ...fns, ...sinks], edges };
}

export const Large: Story = { args: { initial: warm(largeFunctions(), 10), live: true } };

export const Reduced: Story = {
  args: { initial: warm(makeApp("functions", { incident: true }), 8), motion: "reduced" },
};

const long = warm(makeApp("functions"), 6);
export const LongStrings: Story = {
  args: {
    initial: {
      ...long,
      nodes: long.nodes.map((n) => ({
        ...n,
        name: `${n.name}-handler-with-an-unreasonably-long-descriptive-name`,
      })),
    },
  },
};
