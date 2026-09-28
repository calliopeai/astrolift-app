import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import type * as React from "react";

import {
  LONG_PIPELINE,
  LONG_RUN,
  pipelineListProps,
  runHistoryProps,
} from "./pipelines-previews.fixtures";
import { PipelineListView, PipelinesScreen, RunHistoryView } from "./PipelinesScreen";

const meta: Meta = {
  title: "Screens/Pipelines/PipelinesScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

const runsTab = <RunHistoryView {...runHistoryProps()} />;

function pipelinesOn(list: React.ReactNode) {
  return (
    <PipelinesScreen tab="pipelines" onTabChange={() => {}} pipelinesTab={list} runsTab={runsTab} />
  );
}

function runsOn(runs: React.ReactNode) {
  return (
    <PipelinesScreen
      tab="runs"
      onTabChange={() => {}}
      pipelinesTab={<PipelineListView {...pipelineListProps()} />}
      runsTab={runs}
    />
  );
}

export const Full: Story = {
  render: () => pipelinesOn(<PipelineListView {...pipelineListProps()} />),
};

export const Loading: Story = {
  render: () =>
    pipelinesOn(
      <PipelineListView {...pipelineListProps({ state: "loading", rows: [], totalCount: null })} />
    ),
};

export const Empty: Story = {
  render: () =>
    pipelinesOn(
      <PipelineListView {...pipelineListProps({ state: "empty", rows: [], totalCount: 0 })} />
    ),
};

export const EmptyFiltered: Story = {
  render: () =>
    pipelinesOn(
      <PipelineListView
        {...pipelineListProps({
          state: "emptyFiltered",
          rows: [],
          isFiltered: true,
          search: "zzz",
        })}
      />
    ),
};

export const ErrorState: Story = {
  render: () =>
    pipelinesOn(
      <PipelineListView
        {...pipelineListProps({
          state: "error",
          rows: [],
          error: new globalThis.Error("upstream timed out"),
        })}
      />
    ),
};

/** A manual trigger in flight: every Run button is disabled. */
export const Triggering: Story = {
  render: () => pipelinesOn(<PipelineListView {...pipelineListProps({}, { triggering: true })} />),
};

export const LongStrings: Story = {
  render: () =>
    pipelinesOn(
      <PipelineListView {...pipelineListProps({ rows: [LONG_PIPELINE], totalCount: 1 })} />
    ),
};

export const RunHistory: Story = { render: () => runsOn(runsTab) };

/** The pipeline picker is still loading, so the run walk is skipped. */
export const RunHistoryLoading: Story = {
  render: () =>
    runsOn(
      <RunHistoryView
        {...runHistoryProps(
          { state: "empty", rows: [], totalCount: null },
          { options: [], selected: null, loadingOptions: true }
        )}
      />
    ),
};

/** No pipeline registered yet: nothing to pick, nothing to read. */
export const RunHistoryNoPipelines: Story = {
  render: () =>
    runsOn(
      <RunHistoryView
        {...runHistoryProps(
          { state: "empty", rows: [], totalCount: null },
          { options: [], selected: null }
        )}
      />
    ),
};

export const RunHistoryEmpty: Story = {
  render: () =>
    runsOn(<RunHistoryView {...runHistoryProps({ state: "empty", rows: [], totalCount: 0 })} />),
};

export const RunHistoryError: Story = {
  render: () =>
    runsOn(
      <RunHistoryView
        {...runHistoryProps({
          state: "error",
          rows: [],
          error: new globalThis.Error("upstream timed out"),
        })}
      />
    ),
};

export const RunHistoryLongStrings: Story = {
  render: () =>
    runsOn(
      <RunHistoryView
        {...runHistoryProps(
          { rows: [LONG_RUN], totalCount: 1 },
          { options: [LONG_PIPELINE], selected: LONG_PIPELINE.id }
        )}
      />
    ),
};
