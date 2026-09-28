"use client";

import { TopologyScreen } from "@/components/screens/apps/tools/TopologyScreen";
import { useAppTopology } from "@/components/screens/apps/tools/use-app-topology";

import { appPath, useAppChrome } from "../components/app-chrome-context";
import { AppTabs } from "../components/app-tabs";

/** App > Topology tab (#705): the full-page topology graph. */
export function TopologyClient({ slug }: { slug: string }) {
  const chrome = useAppChrome();
  const topology = useAppTopology(slug);
  const appSlug = topology.app?.slug ?? slug;

  return (
    <TopologyScreen
      {...topology}
      workloadsHref={appPath(chrome, appSlug, "workloads")}
      tabs={<AppTabs slug={appSlug} active="topology" />}
    />
  );
}
