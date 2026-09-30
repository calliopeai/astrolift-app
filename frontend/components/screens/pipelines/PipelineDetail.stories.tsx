import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { useLocalListState } from "@/components/list/use-list-state";

import { PipelineDetailScreen, RunGraphView } from "./PipelineDetail";
import { PipelineSecretsView } from "./PipelineSecrets";
import { PIPELINE_SECRETS_LIST } from "./pipelines-list";
import { DETAIL, LONG_RUN, RUN_GRAPH, secretsProps } from "./pipelines-previews.fixtures";

const meta: Meta = {
  title: "Screens/Pipelines/PipelineDetail",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

function Secrets() {
  const list = useLocalListState(PIPELINE_SECRETS_LIST);
  return <PipelineSecretsView {...secretsProps()} list={list} />;
}

const secrets = <Secrets />;
const graph = <RunGraphView {...RUN_GRAPH} />;

export const Full: Story = {
  render: () => <PipelineDetailScreen {...DETAIL} runGraph={graph} secrets={secrets} />,
};

export const Loading: Story = {
  render: () => <PipelineDetailScreen {...DETAIL} runs={[]} runsLoading secrets={secrets} />,
};

export const Empty: Story = {
  render: () => <PipelineDetailScreen {...DETAIL} runs={[]} secrets={secrets} />,
};

export const ErrorState: Story = {
  render: () => (
    <PipelineDetailScreen
      {...DETAIL}
      runs={[]}
      runsError={{ name: "Error", message: "Permission denied while loading this section" }}
      secrets={secrets}
    />
  ),
};

/** The latest run's graph is still loading. */
export const GraphLoading: Story = {
  render: () => (
    <PipelineDetailScreen
      {...DETAIL}
      runGraph={<RunGraphView loading stages={[]} />}
      secrets={secrets}
    />
  ),
};

/** The latest run has no jobs to draw. */
export const GraphEmpty: Story = {
  render: () => (
    <PipelineDetailScreen
      {...DETAIL}
      runGraph={<RunGraphView loading={false} stages={[]} />}
      secrets={secrets}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <PipelineDetailScreen
      {...DETAIL}
      runs={[
        {
          id: LONG_RUN.id,
          runNumber: LONG_RUN.runNumber,
          status: LONG_RUN.status,
          triggerKind: LONG_RUN.triggerKind,
          triggerRef: LONG_RUN.triggerRef,
          startedAt: LONG_RUN.startedAt,
          finishedAt: LONG_RUN.finishedAt,
        },
      ]}
      runGraph={
        <RunGraphView
          loading={false}
          stages={[
            {
              id: "a-job-with-a-very-long-identifier-for-the-integration-suite",
              name: "Integration tests against the staging payment gateway sandbox",
              status: "failure",
              needs: [],
            },
          ]}
        />
      }
      secrets={secrets}
    />
  ),
};

export const LogsTab: Story = {
  render: () => <PipelineDetailScreen {...DETAIL} tab="logs" secrets={secrets} />,
};

export const ArtifactsTab: Story = {
  render: () => <PipelineDetailScreen {...DETAIL} tab="artifacts" secrets={secrets} />,
};

export const TriggersTab: Story = {
  render: () => <PipelineDetailScreen {...DETAIL} tab="triggers" secrets={secrets} />,
};

export const RunnersTab: Story = {
  render: () => <PipelineDetailScreen {...DETAIL} tab="runners" secrets={secrets} />,
};

export const SecretsTab: Story = {
  render: () => <PipelineDetailScreen {...DETAIL} tab="secrets" secrets={secrets} />,
};
