"use client";

import { ScalePopoverView } from "@/components/screens/apps/workloads/ScalePopover";
import { useRunCronNow } from "@/components/screens/apps/workloads/use-run-cron-now";
import { useScaleWorkload } from "@/components/screens/apps/workloads/use-scale-workload";
import { useWorkloadsList } from "@/components/screens/apps/workloads/use-workloads-list";
import { WorkloadRowActions } from "@/components/screens/apps/workloads/WorkloadRowActions";
import { WorkloadsListScreen } from "@/components/screens/apps/workloads/WorkloadsListScreen";

import type { AstroliftWorkload } from "@/graphql/registry/registry.types";

import { appPath, useAppChrome } from "../components/app-chrome-context";

/**
 * The app's Workloads tab, and its scheduled jobs as the cronjob kind. The
 * screen owns the markup; each row's scale popover gets a container so its
 * mutation hook runs only where it is shown, and Run now shares one hook
 * across the rows' menus.
 */
export function WorkloadsListClient({ slug }: { slug: string }) {
  const chrome = useAppChrome();
  const list = useWorkloadsList(slug);
  const run = useRunCronNow(slug);
  return (
    <WorkloadsListScreen
      {...list}
      slug={slug}
      basePath={chrome.basePath}
      renderScale={(w, currentDesired) => (
        <ScalePopover workload={w} currentDesired={currentDesired} />
      )}
      renderRowActions={(w) => (
        <WorkloadRowActions
          workload={w}
          logsHref={`${appPath(chrome, slug, "logs")}?workload=${encodeURIComponent(w.slug)}`}
          {...run}
        />
      )}
    />
  );
}

function ScalePopover({
  workload,
  currentDesired,
}: {
  workload: AstroliftWorkload;
  currentDesired: number;
}) {
  return <ScalePopoverView {...useScaleWorkload(workload, currentDesired)} />;
}
