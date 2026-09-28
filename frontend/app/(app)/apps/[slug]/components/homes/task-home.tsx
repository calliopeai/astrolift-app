"use client";

import { TaskHomeScreen } from "@/components/screens/apps/homes/TaskHome";
import { useTaskRuns } from "@/components/screens/apps/homes/use-task-runs";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";

import { AppTabs } from "../app-tabs";

interface TaskHomeProps {
  slug: string;
  name: string;
  workload: AstroliftWorkload;
}

export function TaskHome({ slug, name, workload }: TaskHomeProps) {
  const runs = useTaskRuns(slug, workload.slug);
  return <TaskHomeScreen {...runs} name={name} tabs={<AppTabs slug={slug} active="overview" />} />;
}
