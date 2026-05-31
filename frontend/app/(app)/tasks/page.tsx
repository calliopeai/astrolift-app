import { PreloadQuery } from "@/lib/apollo";
import { LIST_WORKLOADS } from "@/graphql/registry/registry.queries";

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
 */
export default function TasksPage() {
  return (
    <PreloadQuery query={LIST_WORKLOADS} variables={{}}>
      <TasksClient />
    </PreloadQuery>
  );
}
