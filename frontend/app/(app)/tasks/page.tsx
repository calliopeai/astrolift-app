import { redirect } from "next/navigation";

import { TasksClient } from "./tasks-client";

export const metadata = { title: "Runs · Astrolift" };

/** The old /tasks tabs, and where each went. */
const FORMER_TABS: Record<string, string> = {
  templates: "/tasks/templates",
  recent: "/tasks?kind=task",
  history: "/tasks?kind=task",
  logs: "/tasks?kind=task",
};

/**
 * Agents › Runs (spec 44 §4.1): every agent run, workflow run and container
 * task run in one list. The task templates moved to /tasks/templates; an old
 * `?tab=` link lands where its tab went. No `PreloadQuery`: the list merges
 * source queries whose variables follow the chips and the viewer's modules,
 * so a server preload could not name the cache entries the client reads.
 */
export default async function TasksPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { tab } = await searchParams;
  const former = typeof tab === "string" ? FORMER_TABS[tab] : undefined;
  if (former) redirect(former);
  return <TasksClient />;
}
