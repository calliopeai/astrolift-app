import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { fakeController } from "@/components/data-table/fixtures";
import type { AstroliftAgentBox } from "@/graphql/agents/agents.types";

import { ActiveTasksPanel } from "./ActiveTasksPanel";
import { AgentRegistryPanel } from "./AgentRegistryPanel";
import {
  activeProps,
  boxesProps,
  historyProps,
  LONG_TASKS,
  registryProps,
  screenProps,
} from "./agents-list.fixtures";
import { AGENT_TABS } from "./agents-list-tabs";
import { AgentsScreen } from "./AgentsScreen";
import { BoxesTabView } from "./BoxesTabView";
import { TaskHistoryPanel } from "./TaskHistoryPanel";

const meta: Meta = {
  title: "Screens/Agents/List/AgentsScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

export const Active: Story = {
  render: () => (
    <AgentsScreen
      {...screenProps({ panels: { active: <ActiveTasksPanel {...activeProps()} /> } })}
    />
  ),
};

export const Loading: Story = {
  render: () => (
    <AgentsScreen
      {...screenProps({
        panels: { active: <ActiveTasksPanel {...activeProps({ tasks: [], loading: true })} /> },
      })}
    />
  ),
};

export const Empty: Story = {
  render: () => (
    <AgentsScreen
      {...screenProps({ panels: { active: <ActiveTasksPanel {...activeProps({ tasks: [] })} /> } })}
    />
  ),
};

/** The task tabs have no error state; the Boxes tab's table is the one that shows one. */
export const BoxesFailed: Story = {
  render: () => (
    <AgentsScreen
      {...screenProps({
        tab: "boxes",
        panels: {
          boxes: (
            <BoxesTabView
              {...boxesProps([], {
                controller: fakeController<AstroliftAgentBox>({
                  state: "error",
                  error: new globalThis.Error("upstream timed out"),
                }),
              })}
            />
          ),
        },
      })}
    />
  ),
};

export const Registry: Story = {
  render: () => (
    <AgentsScreen
      {...screenProps({
        tab: "registry",
        panels: { registry: <AgentRegistryPanel {...registryProps()} /> },
      })}
    />
  ),
};

export const History: Story = {
  render: () => (
    <AgentsScreen
      {...screenProps({
        tab: "history",
        panels: { history: <TaskHistoryPanel {...historyProps()} /> },
      })}
    />
  ),
};

export const MetricsPlaceholder: Story = {
  render: () => <AgentsScreen {...screenProps({ tab: "metrics" })} />,
};

export const LogsPlaceholder: Story = {
  render: () => <AgentsScreen {...screenProps({ tab: "logs" })} />,
};

/** Zentinelle enabled: the governance tabs appear, each behind its gate card. */
export const ZentinelleCompliance: Story = {
  render: () => <AgentsScreen {...screenProps({ visibleTabs: AGENT_TABS, tab: "compliance" })} />,
};

export const LongStrings: Story = {
  render: () => (
    <AgentsScreen
      {...screenProps({
        panels: { active: <ActiveTasksPanel {...activeProps({ tasks: LONG_TASKS })} /> },
      })}
    />
  ),
};
