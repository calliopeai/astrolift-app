"use client";

import { ActiveTasksPanel } from "@/components/screens/agents/list/ActiveTasksPanel";
import { AgentRegistryPanel } from "@/components/screens/agents/list/AgentRegistryPanel";
import { AgentsScreen } from "@/components/screens/agents/list/AgentsScreen";
import { TaskHistoryPanel } from "@/components/screens/agents/list/TaskHistoryPanel";
import { useAgentRegistry } from "@/components/screens/agents/list/use-agent-registry";
import {
  useActiveAgentTasks,
  useAgentTaskHistory,
} from "@/components/screens/agents/list/use-agent-tasks";
import { useAgentsScreen } from "@/components/screens/agents/list/use-agents-screen";

import { AgentTheatreContainer } from "./_components/agent-theatre";
import { BoxesTab } from "./boxes-tab";
import { DispatchTab } from "./dispatch-tab";

/**
 * The Agents fleet page. The screen owns the markup; each data-backed tab
 * gets a container here so its queries run only while that tab is shown.
 */
export function AgentsClient() {
  const screen = useAgentsScreen();
  const { orgId } = screen;

  return (
    <AgentsScreen
      {...screen}
      panels={{
        active: <ActiveTab orgId={orgId} />,
        boxes: <BoxesTab orgId={orgId} />,
        dispatch: <DispatchTab orgId={orgId} />,
        history: <HistoryTab orgId={orgId} />,
        registry: <RegistryTab orgId={orgId} />,
        theatre: <AgentTheatreContainer />,
      }}
    />
  );
}

function ActiveTab({ orgId }: { orgId: string }) {
  return <ActiveTasksPanel {...useActiveAgentTasks(orgId)} />;
}

function HistoryTab({ orgId }: { orgId: string }) {
  return <TaskHistoryPanel {...useAgentTaskHistory(orgId)} />;
}

function RegistryTab({ orgId }: { orgId: string }) {
  return <AgentRegistryPanel {...useAgentRegistry(orgId)} />;
}
