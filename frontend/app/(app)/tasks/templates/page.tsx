import { TaskTemplatesClient } from "./task-templates-client";

export const metadata = { title: "Task templates · Astrolift" };

/**
 * Task templates: workloads of kind=task, each with Run now. A container
 * task is a single `batch/v1 Job` run with a defined command (migrations,
 * seed scripts, exports); its runs are on the Runs list at /tasks.
 */
export default function TaskTemplatesPage() {
  return <TaskTemplatesClient />;
}
