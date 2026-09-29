import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import type * as React from "react";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";

import { PIPELINE_RUNS_LIST, PIPELINES_LIST } from "./pipelines-list";
import {
  LONG_PIPELINE,
  LONG_RUN,
  RUN_ROWS,
  pipelineListProps,
  runHistoryProps,
} from "./pipelines-previews.fixtures";
import {
  PipelineListView,
  type PipelineListViewProps,
  PipelinesScreen,
  RunHistoryView,
  type RunHistoryViewProps,
} from "./PipelinesScreen";

const meta: Meta = {
  title: "Screens/Pipelines/PipelinesScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

type Initial = { initial?: Partial<ListState> };

function List({ initial, ...over }: Partial<Omit<PipelineListViewProps, "list">> & Initial) {
  const list = useLocalListState(PIPELINES_LIST, initial);
  return <PipelineListView {...pipelineListProps(over)} list={list} />;
}

function Runs({ initial, ...over }: Partial<Omit<RunHistoryViewProps, "list">> & Initial) {
  const list = useLocalListState(PIPELINE_RUNS_LIST, initial);
  return <RunHistoryView {...runHistoryProps(over)} list={list} />;
}

function pipelinesOn(list: React.ReactNode) {
  return (
    <PipelinesScreen
      tab="pipelines"
      onTabChange={() => {}}
      pipelinesTab={list}
      runsTab={<Runs />}
    />
  );
}

function runsOn(runs: React.ReactNode) {
  return (
    <PipelinesScreen tab="runs" onTabChange={() => {}} pipelinesTab={<List />} runsTab={runs} />
  );
}

export const Full: Story = { render: () => pipelinesOn(<List />) };

export const Loading: Story = {
  render: () => pipelinesOn(<List rows={[]} loading totalCount={null} />),
};

export const Empty: Story = { render: () => pipelinesOn(<List rows={[]} totalCount={0} />) };

export const EmptyFiltered: Story = {
  render: () => pipelinesOn(<List rows={[]} initial={{ q: "zzz" }} />),
};

export const ErrorState: Story = {
  render: () => pipelinesOn(<List rows={[]} error={{ message: "upstream timed out" }} />),
};

/** A manual trigger in flight: every Run action is disabled. */
export const Triggering: Story = { render: () => pipelinesOn(<List triggering />) };

export const LongStrings: Story = {
  render: () => pipelinesOn(<List rows={[LONG_PIPELINE]} totalCount={1} nextCursor="c2" />),
};

export const RunHistory: Story = { render: () => runsOn(<Runs />) };

/** Failed: narrowed on the page in hand, the note says so. */
export const RunHistoryFailed: Story = {
  render: () =>
    runsOn(
      <Runs rows={RUN_ROWS.filter((r) => r.status === "failure")} initial={{ view: "failed" }} />
    ),
};

/** The pipeline picker is still loading, so the run page is skipped. */
export const RunHistoryLoading: Story = {
  render: () =>
    runsOn(<Runs rows={[]} totalCount={null} options={[]} selected={null} loadingOptions />),
};

/** No pipeline registered yet: nothing to pick, nothing to read. */
export const RunHistoryNoPipelines: Story = {
  render: () => runsOn(<Runs rows={[]} totalCount={null} options={[]} selected={null} />),
};

export const RunHistoryEmpty: Story = {
  render: () => runsOn(<Runs rows={[]} totalCount={0} />),
};

export const RunHistoryError: Story = {
  render: () => runsOn(<Runs rows={[]} error={{ message: "upstream timed out" }} />),
};

export const RunHistoryLongStrings: Story = {
  render: () =>
    runsOn(
      <Runs
        rows={[LONG_RUN]}
        totalCount={1}
        options={[LONG_PIPELINE]}
        selected={LONG_PIPELINE.id}
      />
    ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      {runsOn(<Runs rows={[LONG_RUN, ...RUN_ROWS]} />)}
    </div>
  ),
};
