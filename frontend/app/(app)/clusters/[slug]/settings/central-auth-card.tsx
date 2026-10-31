"use client";

import {
  CentralAuthView,
  IngressClassView,
} from "@/components/screens/clusters/settings/CentralAuth";
import { PublicAppsView } from "@/components/screens/clusters/settings/PublicApps";
import {
  useCentralAuth,
  useIngressClass,
  usePublicApps,
} from "@/components/screens/clusters/settings/use-central-auth";
import type { AstroliftTenantCluster } from "@/graphql/clusters/clusters.types";

/** The central-auth card (#2119) wired to one cluster. */
export function CentralAuthCard({ cluster }: { cluster: AstroliftTenantCluster }) {
  return <CentralAuthView {...useCentralAuth(cluster)} />;
}

/** The ingress-class card (#2119) wired to one cluster. */
export function IngressClassCard({ cluster }: { cluster: AstroliftTenantCluster }) {
  return <IngressClassView {...useIngressClass(cluster)} />;
}

/** Whether apps on one cluster may skip the edge login (#2504). */
export function PublicAppsCard({ cluster }: { cluster: AstroliftTenantCluster }) {
  return <PublicAppsView {...usePublicApps(cluster)} />;
}
