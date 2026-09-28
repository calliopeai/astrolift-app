import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { useSimulation } from "../core/use-simulation";
import { VizLegend } from "../core/VizLegend";
import {
  makeWorkflows,
  stepWorkflows,
  type BranchState,
  type FanoutStation,
  type SupervisorStation,
  type WorkflowLine,
  type WorkflowSnapshot,
  type WorkflowTrain,
} from "../core/workflow-model";

import {
  WORKFLOW_TRANSIT_LEGEND,
  WorkflowTransit,
  type WorkflowTransitProps,
} from "./WorkflowTransit";

function Shell(props: WorkflowTransitProps) {
  return (
    <div className="bg-card max-w-3xl space-y-3 rounded-md border p-4">
      <WorkflowTransit {...props} />
      <VizLegend items={WORKFLOW_TRANSIT_LEGEND} motion={props.motion} />
    </div>
  );
}

function Simulated({
  initial,
  ...rest
}: Omit<WorkflowTransitProps, "snapshot"> & { initial: WorkflowSnapshot }) {
  const snapshot = useSimulation(initial, stepWorkflows, { intervalMs: 1200, seed: 7 });
  return <Shell {...rest} snapshot={snapshot} />;
}

function quiet(): WorkflowSnapshot {
  const s = makeWorkflows({ trainsPerLine: 0 });
  return { ...s, segments: s.segments.map((seg) => ({ ...seg, rate: 0, backlog: 0 })) };
}

function incident(): WorkflowSnapshot {
  const s = makeWorkflows({ backedUp: true, trainsPerLine: 4, seed: 5 });
  return {
    ...s,
    trains: s.trains.map((t, i) => {
      const line = s.lines.find((l) => l.id === t.lineId)!;
      const gate = line.stations.findIndex((st) => st.kind === "gate");
      if (i % 4 === 0) return { ...t, state: "failed" as const, progress: 0 };
      if (i % 4 === 1 && gate >= 0) return { ...t, at: gate, progress: 0, state: "held" as const };
      return t;
    }),
  };
}

function longStrings(): WorkflowSnapshot {
  const s = makeWorkflows({ seed: 3 });
  return {
    ...s,
    lines: s.lines.map((l, i) => ({
      ...l,
      name: i === 0 ? "Quarterly compliance evidence collection and export" : l.name,
      stations: l.stations.map((st, j) => ({
        ...st,
        name: j % 2 ? `${st.name} against every regional tenant database` : st.name,
      })),
    })),
    trains: s.trains.map((t) => ({
      ...t,
      label: `${t.label} release-candidate-2026-09-28-hotfix`,
    })),
  };
}

/* ---------- Shaped workflows: loops, fanout, supervisor, nested ---------- */

/** Only the shaped lines: Feature delivery l0, Research digest l1, Support triage l2, Quarterly close l3 and its child Reconcile l4. */
const shaped = (seed = 11) => makeWorkflows({ lines: 0, shapes: true, seed });

/** Keep the named lines (and their runs and segments) of a snapshot. */
function only(s: WorkflowSnapshot, ids: string[]): WorkflowSnapshot {
  const keep = new Set(ids);
  return {
    ...s,
    lines: s.lines.filter((l) => keep.has(l.id)),
    trains: s.trains.filter((t) => keep.has(t.lineId)),
    segments: s.segments.filter((g) => keep.has(g.lineId)),
  };
}

const run = (line: WorkflowLine, id: string, over: Partial<WorkflowTrain>): WorkflowTrain => ({
  id,
  lineId: line.id,
  label: `#${id.replace(/\D/g, "").padStart(4, "5")}`,
  at: 0,
  progress: 0,
  state: "moving",
  startedAt: Date.UTC(2026, 8, 28, 13, 40),
  round: 1,
  attempt: 1,
  ...over,
});

/** Feature delivery, two runs deep in rework: one on round 3 of 5 at Test, one near the Review bound. */
function thirdRound(): WorkflowSnapshot {
  const s = only(shaped(), ["l0"]);
  const line = s.lines[0];
  const [test, review] = line.loops!;
  return {
    ...s,
    trains: [
      run(line, "r1", { at: 2, progress: 0.35, round: 3, loopRounds: { [test.id]: 2 } }),
      run(line, "r2", {
        at: 3,
        state: "held",
        round: 3,
        loopRounds: { [test.id]: 1, [review.id]: 1 },
      }),
      run(line, "r3", { at: test.from, onLoopId: test.id, loopProgress: 0.6 }),
      run(line, "r4", { at: 5, progress: 0.4, attempt: 2, round: 2, loopRounds: { [test.id]: 1 } }),
    ],
  };
}

/** Research digest: a run's fanout almost settled, one straggler branch still running, the next run waiting. */
function straggler(branches = 6): WorkflowSnapshot {
  const s = only(shaped(), ["l1"]);
  const line = s.lines[0];
  const holder = run(line, "r10", { at: 1, progress: 0.75 });
  const states: BranchState[] = Array.from({ length: branches }, (_, b) =>
    b === 3 ? "running" : b === 5 ? "failed" : "succeeded"
  );
  const fan: FanoutStation = {
    ...(line.stations[1] as FanoutStation),
    runId: holder.id,
    branches: states.map((state, b) => ({
      id: `${holder.id}b${b}`,
      label: `Source ${b + 1}`,
      state,
    })),
  };
  return {
    ...s,
    lines: [{ ...line, stations: line.stations.map((st, i) => (i === 1 ? fan : st)) }],
    trains: [
      holder,
      run(line, "r11", { at: 1, state: "held" }),
      run(line, "r12", { at: 3, progress: 0.4 }),
    ],
  };
}

/** Support triage: four of five workers busy on a run's six sub-tasks. */
function supervisorBusy(): WorkflowSnapshot {
  const s = only(shaped(), ["l2"]);
  const line = s.lines[0];
  const holder = run(line, "r20", {
    at: 1,
    subtasks: { total: 6, done: 2 },
    progress: (2 / 6) * 0.9,
  });
  const loads = [0.9, 0.7, 0.55, 0.35, 0.1];
  const sup = line.stations[1] as SupervisorStation;
  const workers = sup.workers.map((w, k) => ({
    ...w,
    busy: k < 4,
    load: loads[k],
    runId: k < 4 ? holder.id : undefined,
  }));
  return {
    ...s,
    lines: [
      { ...line, stations: line.stations.map((st, i) => (i === 1 ? { ...sup, workers } : st)) },
    ],
    trains: [holder, run(line, "r21", { at: 0, progress: 0.6 })],
  };
}

/** Quarterly close with its Reconcile child line; the child run is at Investigate. */
function nested(): WorkflowSnapshot {
  const s = only(shaped(), ["l3", "l4"]);
  const [close, reconcile] = s.lines;
  const parent = run(close, "r30", { at: 1, progress: 0.45, childRunId: "r30/child" });
  const child = run(reconcile, "r30/child", { at: 1, progress: 0.5, parentRunId: parent.id });
  child.label = parent.label;
  return { ...s, trains: [parent, child, run(close, "r31", { at: 2, state: "held" })] };
}

function longShaped(): WorkflowSnapshot {
  const s = shaped();
  return {
    ...s,
    lines: s.lines.map((l) => ({
      ...l,
      name: `${l.name} for every regional business unit`,
      stations: l.stations.map((st, j) => ({
        ...st,
        name: j % 2 ? `${st.name} across all tenant ledgers` : st.name,
      })),
    })),
  };
}

const meta: Meta<typeof Shell> = {
  title: "Viz/Workflow/WorkflowTransit",
  component: Shell,
  args: { motion: "full", flowParticles: true, onSelectRun: () => {}, onSelectStation: () => {} },
};
export default meta;

type Story = StoryObj<typeof Shell>;

export const Live: Story = {
  render: (args) => <Simulated {...args} initial={makeWorkflows()} />,
};

export const Quiet: Story = { args: { snapshot: quiet() } };

export const Incident: Story = { args: { snapshot: incident() } };

export const Large: Story = {
  render: (args) => (
    <Simulated {...args} initial={makeWorkflows({ lines: 8, trainsPerLine: 6, seed: 21 })} />
  ),
};

export const Reduced: Story = { args: { snapshot: incident(), motion: "reduced" } };

export const NoParticles: Story = { args: { snapshot: makeWorkflows(), flowParticles: false } };

export const LongStrings: Story = { args: { snapshot: longStrings() } };

/** Feature delivery running live: fails at Test send runs back to Code, Review rejects too, Deploy retries. */
export const FeatureDeliveryLive: Story = {
  render: (args) => <Simulated {...args} initial={only(shaped(), ["l0"])} />,
};

/** Every shape at once, live, under the classic lines. */
export const ShapesLive: Story = {
  render: (args) => <Simulated {...args} initial={makeWorkflows({ shapes: true })} />,
};

/** Round badges: "3 / 5" at Test, amber "2 / 3" at Review (one round from the bound), a run riding back, a Deploy retry. */
export const ThirdRoundNearBound: Story = { args: { snapshot: thirdRound() } };

/** Five branches at the join, one failed; Source 4 is the straggler still lit. */
export const FanOutStraggler: Story = { args: { snapshot: straggler() } };

/** Eleven branches: eight sidings and a "+3". */
export const FanOutWide: Story = { args: { snapshot: straggler(11) } };

export const SupervisorBusy: Story = { args: { snapshot: supervisorBusy() } };

export const NestedExpanded: Story = {
  args: { snapshot: nested(), defaultExpanded: ["l3s1"] },
};

export const ShapesReduced: Story = {
  args: { snapshot: thirdRound(), motion: "reduced" },
  render: (args) => (
    <div className="space-y-6">
      <Shell {...args} />
      <Shell {...args} snapshot={straggler()} />
      <Shell {...args} snapshot={supervisorBusy()} />
      <Shell {...args} snapshot={nested()} defaultExpanded={["l3s1"]} />
    </div>
  ),
};

export const At768: Story = {
  render: (args) => (
    <div style={{ width: 768 }}>
      <Simulated {...args} initial={shaped()} defaultExpanded={["l3s1"]} />
    </div>
  ),
};

export const LongStationNames: Story = {
  args: { snapshot: longShaped(), defaultExpanded: ["l3s1"] },
};
