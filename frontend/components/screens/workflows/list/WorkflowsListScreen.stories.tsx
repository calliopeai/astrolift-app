import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { useState } from "react";

import { WorkflowInstancesPanelView } from "./WorkflowInstancesPanel";
import { WorkflowsListScreen } from "./WorkflowsListScreen";
import type { WorkflowTab } from "./use-workflows-list";
import {
  CONFIGURED,
  LONG_CONFIGURED,
  LONG_DEFINITION,
  LONG_RUN,
  listProps,
  loadError,
  panelProps,
} from "./workflows-list.fixtures";

const meta: Meta = {
  title: "Screens/Workflows/List/WorkflowsListScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

/** Every tab populated; the tab bar switches tabs locally. */
export const Full: Story = {
  render: function Render() {
    const [tab, setTab] = useState<WorkflowTab>("workflows");
    return (
      <WorkflowsListScreen
        {...listProps({ tab, setTab })}
        instancesPanel={<WorkflowInstancesPanelView {...panelProps()} />}
      />
    );
  },
};

export const Loading: Story = {
  render: () => (
    <WorkflowsListScreen
      {...listProps(
        { defsLoading: true, definitions: [], repositoryWorkflows: [] },
        { state: "loading", rows: [], totalCount: null }
      )}
    />
  ),
};

export const Empty: Story = {
  render: () => (
    <WorkflowsListScreen
      {...listProps(
        { definitions: [], repositoryWorkflows: [] },
        { state: "empty", rows: [], totalCount: 0 }
      )}
    />
  ),
};

export const LoadFailed: Story = {
  render: () => (
    <WorkflowsListScreen
      {...listProps(
        { defsError: loadError("workflowDefinitions: permission denied") },
        { state: "error", rows: [], totalCount: 0, error: loadError() }
      )}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <WorkflowsListScreen
      {...listProps(
        { repositoryWorkflows: [LONG_DEFINITION] },
        { rows: [LONG_CONFIGURED, ...CONFIGURED], totalCount: 3 }
      )}
    />
  ),
};

export const ReadOnly: Story = {
  render: () => (
    <WorkflowsListScreen
      {...listProps({ entitlement: { canCreate: false, canManage: false, canRun: false } })}
    />
  ),
};

export const Running: Story = {
  render: () => (
    <WorkflowsListScreen
      {...listProps({ tab: "running" })}
      instancesPanel={<WorkflowInstancesPanelView {...panelProps()} />}
    />
  ),
};

export const RunningLongStrings: Story = {
  render: () => (
    <WorkflowsListScreen
      {...listProps({
        tab: "running",
        canViewPlatformRuns: false,
        definitionRuns: { loading: false, error: undefined, running: [LONG_RUN], historical: [] },
      })}
    />
  ),
};

export const RunningEmpty: Story = {
  render: () => (
    <WorkflowsListScreen
      {...listProps({
        tab: "running",
        canViewPlatformRuns: false,
        definitionRuns: { loading: false, error: undefined, running: [], historical: [] },
      })}
    />
  ),
};

export const RunningLoadFailed: Story = {
  render: () => (
    <WorkflowsListScreen
      {...listProps({
        tab: "running",
        canViewPlatformRuns: false,
        definitionRuns: {
          loading: false,
          error: loadError("workflowDefinitionRuns: upstream timed out"),
          running: [],
          historical: [],
        },
      })}
    />
  ),
};

export const Templates: Story = {
  render: () => <WorkflowsListScreen {...listProps({ tab: "definitions" })} />,
};

export const TemplatesEmpty: Story = {
  render: () => <WorkflowsListScreen {...listProps({ tab: "definitions", definitions: [] })} />,
};

export const History: Story = {
  render: () => <WorkflowsListScreen {...listProps({ tab: "history" })} />,
};

export const HistoryLoading: Story = {
  render: () => (
    <WorkflowsListScreen
      {...listProps({
        tab: "history",
        runsLoading: true,
        historyRuns: [],
        definitionRuns: { loading: true, error: undefined, running: [], historical: [] },
      })}
    />
  ),
};

export const HistoryEmpty: Story = {
  render: () => (
    <WorkflowsListScreen
      {...listProps({
        tab: "history",
        historyRuns: [],
        definitionRuns: { loading: false, error: undefined, running: [], historical: [] },
      })}
    />
  ),
};
