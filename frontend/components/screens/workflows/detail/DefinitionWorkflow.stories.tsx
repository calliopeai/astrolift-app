import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import {
  DefinitionBuildView,
  DefinitionObserveView,
  DefinitionRunPanelView,
  DefinitionRunView,
  DefinitionWorkflowScreen,
} from "./DefinitionWorkflow";
import { GateReviewView } from "./GateReview";
import {
  DAG,
  DEFINITION,
  GATES,
  LONG_DAG,
  LONG_DEFINITION,
  OBSERVE,
  PANEL,
  RUN,
  RUNS,
  SCREEN,
} from "./workflow-detail-a.fixtures";
import { WorkflowRunDagView } from "./WorkflowRunDag";

const meta: Meta = {
  title: "Screens/Workflows/Detail/DefinitionWorkflow",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

const panel = (run = PANEL.run) => (
  <DefinitionRunPanelView
    {...PANEL}
    run={run}
    terminal={run?.status !== "running"}
    runDag={<WorkflowRunDagView {...DAG} gateReview={<GateReviewView {...GATES} />} />}
  />
);

export const Build: Story = {
  render: () => (
    <DefinitionWorkflowScreen {...SCREEN}>
      <DefinitionBuildView definition={DEFINITION} />
    </DefinitionWorkflowScreen>
  ),
};

/** Built-in catalogue workflow: no repository, manifest or ref. */
export const BuildBuiltIn: Story = {
  render: () => {
    const definition = { ...DEFINITION, sourceRepo: "", sourcePath: "", sourceRef: "" };
    return (
      <DefinitionWorkflowScreen {...SCREEN} definition={definition}>
        <DefinitionBuildView definition={definition} />
      </DefinitionWorkflowScreen>
    );
  },
};

export const Run: Story = {
  render: () => (
    <DefinitionWorkflowScreen {...SCREEN}>
      <DefinitionRunView {...RUN} />
    </DefinitionWorkflowScreen>
  ),
};

/** A disabled workflow that has never run, seen by someone who may not run it. */
export const RunNeverRun: Story = {
  render: () => {
    const definition = { ...DEFINITION, isEnabled: false };
    return (
      <DefinitionWorkflowScreen {...SCREEN} definition={definition}>
        <DefinitionRunView {...RUN} definition={definition} latest={null} canRun={false} />
      </DefinitionWorkflowScreen>
    );
  },
};

export const RunDispatching: Story = {
  render: () => (
    <DefinitionWorkflowScreen {...SCREEN}>
      <DefinitionRunView {...RUN} running />
    </DefinitionWorkflowScreen>
  ),
};

export const Observe: Story = {
  render: () => (
    <DefinitionWorkflowScreen {...SCREEN}>
      <DefinitionObserveView {...OBSERVE} renderRunPanel={(run) => panel(run)} />
    </DefinitionWorkflowScreen>
  ),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByText(RUNS[2].temporalWorkflowId));
    await expect(canvas.getAllByText(RUNS[2].temporalWorkflowId)).toHaveLength(2);
  },
};

/** Opened from a link to a specific run (`?run=`). */
export const ObserveRequestedRun: Story = {
  render: () => (
    <DefinitionWorkflowScreen {...SCREEN}>
      <DefinitionObserveView
        {...OBSERVE}
        requestedRunGuid={RUNS[3].guid}
        renderRunPanel={(run) => panel(run)}
      />
    </DefinitionWorkflowScreen>
  ),
};

export const ObserveLoading: Story = {
  render: () => (
    <DefinitionWorkflowScreen {...SCREEN}>
      <DefinitionObserveView {...OBSERVE} runs={[]} loading renderRunPanel={panel} />
    </DefinitionWorkflowScreen>
  ),
};

export const ObserveEmpty: Story = {
  render: () => (
    <DefinitionWorkflowScreen {...SCREEN}>
      <DefinitionObserveView {...OBSERVE} runs={[]} renderRunPanel={panel} />
    </DefinitionWorkflowScreen>
  ),
};

export const ObserveFailed: Story = {
  render: () => (
    <DefinitionWorkflowScreen {...SCREEN}>
      <DefinitionObserveView
        {...OBSERVE}
        runs={[]}
        error={{ message: "workflowDefinitionRuns: upstream timed out" } as typeof OBSERVE.error}
        renderRunPanel={panel}
      />
    </DefinitionWorkflowScreen>
  ),
};

/** The run panel while the engine timeline loads, then when it failed or is empty. */
export const RunPanelTimelineStates: Story = {
  render: () => (
    <div className="grid gap-4 p-4">
      <DefinitionRunPanelView {...PANEL} detail={null} detailLoading runDag={null} />
      <DefinitionRunPanelView
        {...PANEL}
        detail={null}
        detailError={{ message: "Engine unreachable" } as typeof PANEL.detailError}
        runDag={null}
      />
      <DefinitionRunPanelView
        {...PANEL}
        detail={PANEL.detail && { ...PANEL.detail, history: [] }}
        cancelling
        runDag={null}
      />
    </div>
  ),
};

export const Loading: Story = {
  render: () => <DefinitionWorkflowScreen {...SCREEN} definition={null} loading />,
};

/** No workflow with this slug, or no access to it. */
export const NotFound: Story = {
  render: () => <DefinitionWorkflowScreen {...SCREEN} slug="no-such-workflow" definition={null} />,
};

export const LoadFailed: Story = {
  render: () => (
    <DefinitionWorkflowScreen
      {...SCREEN}
      definition={null}
      error={{ message: "Network error: Failed to fetch" }}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <DefinitionWorkflowScreen {...SCREEN} slug={LONG_DEFINITION.slug} definition={LONG_DEFINITION}>
      <DefinitionBuildView definition={LONG_DEFINITION} />
      <DefinitionObserveView
        {...OBSERVE}
        runs={RUNS.map((r) => ({
          ...r,
          temporalWorkflowId: `${r.temporalWorkflowId}-${LONG_DEFINITION.slug}`,
        }))}
        renderRunPanel={(run) => (
          <DefinitionRunPanelView
            {...PANEL}
            run={run}
            runDag={<WorkflowRunDagView {...LONG_DAG} />}
          />
        )}
      />
    </DefinitionWorkflowScreen>
  ),
};
