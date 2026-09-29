import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { makeFleet, stepFleet, type FleetSnapshot } from "../core/fleet-model";
import { useSimulation } from "../core/use-simulation";
import { VizLegend } from "../core/VizLegend";

import { FLEET_MANIFEST_LEGEND, FleetManifest } from "./FleetManifest";
import { makeRuns, stepRuns } from "./manifest";

function step(prev: FleetSnapshot, rng: () => number, dtMs: number): FleetSnapshot {
  return stepRuns(stepFleet(prev, rng, dtMs), rng);
}

function Board({
  initial,
  motion = "full",
  live = false,
}: {
  initial: FleetSnapshot;
  motion?: "full" | "reduced";
  live?: boolean;
}) {
  const snapshot = useSimulation(initial, step, { paused: !live, intervalMs: 1000 });
  const [selected, setSelected] = React.useState<string | undefined>();
  return (
    <div className="bg-background space-y-3 p-4">
      <FleetManifest
        snapshot={snapshot}
        motion={motion}
        selectedAgentId={selected}
        onSelectAgent={setSelected}
        className="rounded-sm border"
      />
      <VizLegend items={FLEET_MANIFEST_LEGEND} motion={motion} />
    </div>
  );
}

function withRuns(snapshot: FleetSnapshot, options: Parameters<typeof makeRuns>[1]): FleetSnapshot {
  return { ...snapshot, runs: makeRuns(snapshot, options) };
}

const live = withRuns(makeFleet(), { count: 12 });

const quiet = withRuns(makeFleet({ idle: true }), {
  count: 4,
  mix: { queued: 1, landed: 2 },
});

const incident = withRuns(makeFleet({ failing: 0.3 }), {
  count: 14,
  seed: 11,
  mix: { queued: 2, holding: 3, in_flight: 2, failed: 3, landed: 1 },
});

const large = withRuns(makeFleet({ clusters: 6, agentsPerCluster: 36 }), {
  count: 80,
  seed: 5,
});

function longStrings(): FleetSnapshot {
  const base = makeFleet();
  const s: FleetSnapshot = {
    ...base,
    clusters: base.clusters.map((c) => ({
      ...c,
      name: `${c.name}-customer-dedicated-eks-cluster-in-a-very-long-region-name`,
    })),
    agents: base.agents.map((a) => ({
      ...a,
      name: `${a.name}-with-an-extremely-long-agent-identifier-from-a-template`,
    })),
  };
  return {
    ...s,
    runs: makeRuns(s, { count: 8, mix: { holding: 2, in_flight: 1, queued: 1 } }).map((r) => ({
      ...r,
      label: `${r.label}: quarterly reconciliation for every connected billing account`,
      holdReason: r.holdReason
        ? `${r.holdReason} from the change advisory board, pending two reviewers`
        : undefined,
    })),
  };
}

const meta: Meta<typeof Board> = {
  title: "Viz/Fleet/FleetManifest",
  component: Board,
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj<typeof Board>;

export const Live: Story = { args: { initial: live, live: true } };
export const Quiet: Story = { args: { initial: quiet } };
export const Incident: Story = { args: { initial: incident, live: true } };
export const Large: Story = { args: { initial: large, live: true } };
export const Reduced: Story = { args: { initial: incident, motion: "reduced", live: true } };
export const LongStrings: Story = { args: { initial: longStrings() } };
export const Empty: Story = { args: { initial: { ...makeFleet(), runs: [] } } };
