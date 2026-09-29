"use client";

import { TaskRunDetail } from "@/components/screens/tasks/TaskRunDetail";
import { useTaskRunDetail } from "@/components/screens/tasks/use-task-run-detail";

/** Task run detail (#1106, #1118). */
export function TaskRunDetailClient({ id }: { id: string }) {
  return <TaskRunDetail id={id} {...useTaskRunDetail(id)} />;
}
