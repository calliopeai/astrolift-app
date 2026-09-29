import { PreloadQuery } from "@/lib/apollo";
import { GET_TASK_RUN } from "@/graphql/lifecycle/lifecycle.queries";

import { TaskRunDetailClient } from "./task-run-detail-client";

export const metadata = { title: "Task run · Astrolift" };

/**
 * Task run detail (#1106, #1118): a container task run on the run page, the
 * drill-in target for a task run row on Runs. Warms the by-id query the
 * client reads.
 */
export default async function TaskRunDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <PreloadQuery query={GET_TASK_RUN} variables={{ id }}>
      <TaskRunDetailClient id={id} />
    </PreloadQuery>
  );
}
