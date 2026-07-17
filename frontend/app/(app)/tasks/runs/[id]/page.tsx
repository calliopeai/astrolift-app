import { PreloadQuery } from "@/lib/apollo";
import { LIST_TASK_RUNS } from "@/graphql/lifecycle/lifecycle.queries";

import { TaskRunDetailClient } from "./task-run-detail-client";

export const metadata = { title: "Task run · Astrolift" };

/**
 * Task run detail (#1106) — the drill-in target for a /tasks Recent/History
 * row. No singular backend query exists, so it warms and reuses the same
 * LIST_TASK_RUNS window the list uses.
 */
export default async function TaskRunDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <PreloadQuery query={LIST_TASK_RUNS} variables={{ limit: 100 }}>
      <TaskRunDetailClient id={id} />
    </PreloadQuery>
  );
}
