"use client";

import { AgentTheatreContainer } from "../../_components/agent-theatre";
import { AgentObserveScreen } from "@/components/screens/agents/detail/AgentObserve";
import { AgentTaskLogsView } from "@/components/screens/agents/detail/AgentTaskLogs";
import {
  type AgentTask,
  useAgentObserve,
} from "@/components/screens/agents/detail/use-agent-observe";
import { useAgentTaskLogs } from "@/components/screens/agents/detail/use-agent-task-logs";

/** Observe tab container: the org's runs, the live theatre, the latest run's logs. */
export function ObserveContent({ orgId }: { orgId: string }) {
  const observe = useAgentObserve(orgId);
  return (
    <AgentObserveScreen
      {...observe}
      theatre={<AgentTheatreContainer />}
      logs={observe.latest ? <TaskLogs task={observe.latest} /> : null}
    />
  );
}

/** Polls logs only while a latest run is shown. */
function TaskLogs({ task }: { task: AgentTask }) {
  const logs = useAgentTaskLogs(task.id);
  return <AgentTaskLogsView task={task} {...logs} />;
}
