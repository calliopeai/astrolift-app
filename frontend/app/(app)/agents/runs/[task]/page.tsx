import { AgentTaskDetail } from "./task-detail-client";

export const metadata = { title: "Agent run · Astrolift" };

/**
 * Agent run detail — one AgentTask by id (#1105). The drill-down target for
 * every dispatch/run line item (the fleet Active / History tabs). Server
 * component: it only awaits the route param and hands the id to the client
 * detail, which reads `agentTask(id)` + `agentTaskLogs(id)`. The `[task]`
 * dynamic segment is shared with the sibling `runs/[task]/vnc/` pop-out, so the
 * slug name doesn't collide with the `[agentSlug]` detail route one level up.
 */
export default async function AgentRunDetailPage({
  params,
}: {
  params: Promise<{ task: string }>;
}) {
  const { task } = await params;
  return <AgentTaskDetail taskId={task} />;
}
