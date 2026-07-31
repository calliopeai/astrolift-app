import { TasksClient } from "./tasks-client";

export const metadata = { title: "Tasks · Astrolift" };

/**
 * Tasks — one-off container task execution.
 *
 * Tasks are the third execution primitive after Jobs (scheduled/recurring)
 * and Deployments (long-running services). A Task is a single container run
 * with a defined command: database migrations, seed scripts, data exports,
 * manual interventions — anything that runs once and exits.
 *
 * Maps to Kubernetes `batch/v1 Job` with `completions=1`. Distinct from
 * CronJobs (recurring) and Deployments (persistent replicas).
 *
 * No `PreloadQuery`: both tables walk a cursor whose first request carries
 * `limit` plus a filter the controller owns, so a variable-less preload of
 * the deprecated flat list was a guaranteed cache miss — an SSR round trip
 * *and* the client fetch. DataTable's skeleton covers the first paint.
 */
export default function TasksPage() {
  return <TasksClient />;
}
