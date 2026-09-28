import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { useSimulation } from "../core/use-simulation";
import { VizLegend } from "../core/VizLegend";
import {
  makeWorkflows,
  stepWorkflows,
  type FanoutStation,
  type WorkflowSnapshot,
  type WorkflowTrain,
} from "../core/workflow-model";

import { WORKFLOW_ISOMETRIC_LEGEND, WorkflowIsometric } from "./WorkflowIsometric";

type Scenario =
  | "live"
  | "quiet"
  | "incident"
  | "large"
  | "long"
  | "shapes"
  | "feature"
  | "nearBound"
  | "straggler"
  | "supervisor"
  | "nested";

const NOW = Date.UTC(2026, 8, 28, 14, 0, 0);

/** The shaped lines alone: l0 Feature delivery, l1 Research digest, l2 Support triage, l3 Quarterly close, l4 its child Reconcile. */
function shaped(ids?: string[]): WorkflowSnapshot {
  const s = makeWorkflows({ lines: 0, shapes: true, now: NOW });
  if (!ids) return s;
  const keep = new Set(ids);
  return {
    ...s,
    lines: s.lines.filter((l) => keep.has(l.id)),
    trains: s.trains.filter((t) => keep.has(t.lineId)),
    segments: s.segments.filter((x) => keep.has(x.lineId)),
  };
}

const run = (over: Partial<WorkflowTrain> & Pick<WorkflowTrain, "id" | "lineId" | "at">) => ({
  label: `#${over.id}`,
  progress: 0,
  state: "moving" as const,
  startedAt: NOW - 600_000,
  round: 1,
  attempt: 1,
  ...over,
});

/** Feature delivery with runs one round (or attempt) from their bounds, and one climbing back. */
function nearBound(): WorkflowSnapshot {
  const s = shaped(["l0"]);
  const [test, review] = s.lines[0].loops!;
  return {
    ...s,
    trains: [
      run({
        id: "5310",
        lineId: "l0",
        at: 2,
        progress: 0.55,
        round: 5,
        loopRounds: { [test.id]: 3, [review.id]: 1 },
      }),
      run({
        id: "5318",
        lineId: "l0",
        at: 3,
        state: "held",
        round: 3,
        loopRounds: { [test.id]: 1, [review.id]: 1 },
      }),
      run({
        id: "5322",
        lineId: "l0",
        at: 2,
        round: 2,
        loopRounds: { [test.id]: 1 },
        onLoopId: test.id,
        loopProgress: 0.5,
      }),
      run({ id: "5301", lineId: "l0", at: 5, attempt: 2 }),
      run({ id: "5330", lineId: "l0", at: 0, progress: 0.4 }),
    ],
  };
}

/** Research digest: five of six branches settled, one straggler still running, a run waiting its turn. */
function straggler(): WorkflowSnapshot {
  const s = shaped(["l1"]);
  const line = s.lines[0];
  const fan = line.stations[1] as FanoutStation;
  const states = ["succeeded", "succeeded", "failed", "succeeded", "succeeded", "running"] as const;
  const held: FanoutStation = {
    ...fan,
    runId: "7702",
    branches: states.map((state, b) => ({ id: `7702b${b}`, label: `Source ${b + 1}`, state })),
  };
  return {
    ...s,
    lines: [{ ...line, stations: line.stations.map((st, j) => (j === 1 ? held : st)) }],
    trains: [
      run({ id: "7702", lineId: line.id, at: 1, progress: (5 / 6) * 0.9 }),
      run({ id: "7709", lineId: line.id, at: 1, state: "held" }),
      run({ id: "7690", lineId: line.id, at: 2, progress: 0.6 }),
    ],
  };
}

function scenario(kind: Scenario): WorkflowSnapshot {
  switch (kind) {
    case "quiet": {
      const s = makeWorkflows({ trainsPerLine: 0 });
      return { ...s, segments: s.segments.map((x) => ({ ...x, rate: 0, backlog: 0 })) };
    }
    case "incident": {
      const s = makeWorkflows({ backedUp: true, trainsPerLine: 4, seed: 5 });
      return {
        ...s,
        trains: s.trains.map((t, i) =>
          i % 4 === 1 && s.lines.find((l) => l.id === t.lineId)?.stations[t.at]?.kind !== "gate"
            ? { ...t, state: "failed" as const, progress: 0 }
            : t
        ),
      };
    }
    case "large":
      return makeWorkflows({ lines: 8, trainsPerLine: 4, seed: 21 });
    case "long": {
      const s = makeWorkflows();
      return {
        ...s,
        lines: s.lines.map((l, i) => ({
          ...l,
          name: `${l.name} for the enterprise customer onboarding programme ${i}`,
          stations: l.stations.map((st) => ({
            ...st,
            name: `${st.name} with a deliberately long station name`,
          })),
        })),
        trains: s.trains.map((t) => ({ ...t, label: `${t.label}-release-candidate-build` })),
      };
    }
    case "shapes":
      return makeWorkflows({ shapes: true, now: NOW });
    case "feature":
      return shaped(["l0"]);
    case "nearBound":
      return nearBound();
    case "straggler":
      return straggler();
    case "supervisor":
      return shaped(["l2"]);
    case "nested":
      return shaped(["l3", "l4"]);
    default:
      return makeWorkflows();
  }
}

function Demo({
  kind = "live",
  live = true,
  motion = "full",
  flowParticles = true,
  defaultOpen,
}: {
  kind?: Scenario;
  live?: boolean;
  motion?: "full" | "reduced";
  flowParticles?: boolean;
  defaultOpen?: string[];
}) {
  const snapshot = useSimulation(scenario(kind), stepWorkflows, {
    paused: !live,
    intervalMs: 1200,
    seed: 3,
  });
  return (
    <div data-motion={motion} className="bg-card space-y-3 rounded-md border p-4">
      <WorkflowIsometric
        snapshot={snapshot}
        motion={motion}
        flowParticles={flowParticles}
        defaultOpen={defaultOpen}
        onSelectRun={() => {}}
        onSelectStation={() => {}}
      />
      <VizLegend items={WORKFLOW_ISOMETRIC_LEGEND} motion={motion} />
    </div>
  );
}

const meta: Meta<typeof Demo> = {
  title: "Viz/Workflow/WorkflowIsometric",
  component: Demo,
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj<typeof Demo>;

export const Live: Story = { args: { kind: "live" } };
export const Quiet: Story = { args: { kind: "quiet", live: false } };
export const Incident: Story = { args: { kind: "incident", live: false } };
export const Large: Story = { args: { kind: "large" } };
export const Reduced: Story = { args: { kind: "live", motion: "reduced" } };
export const NoParticles: Story = { args: { kind: "live", flowParticles: false } };
export const LongStrings: Story = { args: { kind: "long", live: false } };
export const Tablet: Story = {
  args: { kind: "live" },
  decorators: [
    (S) => (
      <div style={{ width: 768 }}>
        <S />
      </div>
    ),
  ],
};

export const FeatureDelivery: Story = { args: { kind: "feature" } };
export const NearBound: Story = { args: { kind: "nearBound", live: false } };
export const FanoutStraggler: Story = { args: { kind: "straggler", live: false } };
export const Supervisor: Story = { args: { kind: "supervisor" } };
export const Nested: Story = { args: { kind: "nested", defaultOpen: ["l3s1"] } };
export const NestedClosed: Story = { args: { kind: "nested", live: false } };
export const AllShapes: Story = { args: { kind: "shapes", defaultOpen: ["l6s1"] } };
export const ShapesReduced: Story = {
  args: { kind: "shapes", live: false, motion: "reduced", defaultOpen: ["l6s1"] },
};
export const ShapesTablet: Story = {
  args: { kind: "shapes", defaultOpen: ["l6s1"] },
  decorators: [
    (S) => (
      <div style={{ width: 768 }}>
        <S />
      </div>
    ),
  ],
};
