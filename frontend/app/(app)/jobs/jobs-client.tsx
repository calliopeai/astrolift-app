"use client";

import type * as React from "react";

import type { CursorTableController } from "@/components/data-table";
import { CronWorkloadsTable, JobsScreen } from "@/components/screens/jobs/JobsScreen";
import { useCronWorkloadRun, useJobs, type CronWorkload } from "@/components/screens/jobs/use-jobs";

/**
 * Jobs, fleet-wide at /jobs and app-scoped at /apps/[slug]/jobs. The
 * screen owns the markup; the per-app schedules table gets a container
 * here so its environments query only runs while that tab is shown.
 */
export function JobsClient({ appSlug, tabs }: { appSlug?: string; tabs?: React.ReactNode } = {}) {
  const jobs = useJobs(appSlug);

  return (
    <JobsScreen
      {...jobs}
      appSlug={appSlug}
      tabs={tabs}
      cronWorkloads={
        appSlug ? (
          <CronWorkloads
            appSlug={appSlug}
            controller={jobs.schedulesController}
            onRan={jobs.onRan}
          />
        ) : undefined
      }
    />
  );
}

function CronWorkloads({
  appSlug,
  controller,
  onRan,
}: {
  appSlug: string;
  controller: CursorTableController<CronWorkload>;
  onRan: () => void;
}) {
  return (
    <CronWorkloadsTable
      appSlug={appSlug}
      controller={controller}
      {...useCronWorkloadRun(appSlug, onRan)}
    />
  );
}
