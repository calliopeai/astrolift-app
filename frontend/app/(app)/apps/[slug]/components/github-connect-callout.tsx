"use client";

import { GithubConnectCalloutView } from "@/components/screens/apps/overview/GithubConnectCallout";
import { useSourceConnections } from "@/components/screens/apps/overview/use-source-connections";

/** Personal GitHub connect prompt wired to the viewer's source connections. */
export function GithubConnectCallout({ sourceKind }: { sourceKind: string }) {
  return <GithubConnectCalloutView sourceKind={sourceKind} {...useSourceConnections()} />;
}
