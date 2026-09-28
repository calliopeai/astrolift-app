"use client";

import { ScalePopoverView } from "@/components/screens/apps/workloads/ScalePopover";
import { useScaleWorkload } from "@/components/screens/apps/workloads/use-scale-workload";
import { useWorkloadsList } from "@/components/screens/apps/workloads/use-workloads-list";
import { WorkloadsListScreen } from "@/components/screens/apps/workloads/WorkloadsListScreen";

import { useAppChrome } from "../components/app-chrome-context";
import { AppTabs } from "../components/app-tabs";

/**
 * The app's workloads tab. The screen owns the markup; each row's scale
 * popover gets a container so its mutation hook runs only where it is shown.
 */
export function WorkloadsListClient({ slug }: { slug: string }) {
  const chrome = useAppChrome();
  const list = useWorkloadsList(slug);
  return (
    <WorkloadsListScreen
      {...list}
      slug={slug}
      basePath={chrome.basePath}
      tabs={list.app ? <AppTabs slug={list.app.slug} active="workloads" /> : null}
      renderScale={(w, currentDesired) => (
        <ScalePopover workloadId={w.id} workloadName={w.name} currentDesired={currentDesired} />
      )}
    />
  );
}

function ScalePopover({
  workloadId,
  workloadName,
  currentDesired,
}: {
  workloadId: string;
  workloadName: string;
  currentDesired: number;
}) {
  return <ScalePopoverView {...useScaleWorkload(workloadId, workloadName, currentDesired)} />;
}
