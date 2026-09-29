"use client";

import { TopologyScreen } from "@/components/screens/apps/tools/TopologyScreen";
import { useAppTopology } from "@/components/screens/apps/tools/use-app-topology";

import { appPath, useAppChrome } from "../components/app-chrome-context";

/** The overview's topology panel (#705; the Topology tab folded into Overview). */
export function TopologyClient({ slug }: { slug: string }) {
  const chrome = useAppChrome();
  const topology = useAppTopology(slug);
  const appSlug = topology.app?.slug ?? slug;

  return <TopologyScreen {...topology} workloadsHref={appPath(chrome, appSlug, "workloads")} />;
}
