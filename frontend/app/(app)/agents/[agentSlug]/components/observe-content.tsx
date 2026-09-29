"use client";

import { AgentObserveScreen } from "@/components/screens/agents/detail/AgentObserve";
import { AgentTaskLogsView } from "@/components/screens/agents/detail/AgentTaskLogs";
import {
  type AgentTask,
  useAgentObserve,
} from "@/components/screens/agents/detail/use-agent-observe";
import { useAgentTaskLogs } from "@/components/screens/agents/detail/use-agent-task-logs";

import { useFramedAgent } from "./framed-agent";

/** Logs & metrics › Live runs: this agent's latest run and its log. */
export function ObserveContent() {
  const { agent, orgId } = useFramedAgent();
  const observe = useAgentObserve(agent, orgId);
  return (
    <AgentObserveScreen
      {...observe}
      slug={agent.slug}
      logs={observe.latest ? <TaskLogs key={observe.latest.id} task={observe.latest} /> : null}
    />
  );
}

/** Polls logs only while a latest run is shown. */
function TaskLogs({ task }: { task: AgentTask }) {
  return <AgentTaskLogsView task={task} {...useAgentTaskLogs(task.id)} />;
}
