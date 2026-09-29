"use client";

import {
  CentralAuthView,
  IngressClassView,
} from "@/components/screens/clusters/settings/CentralAuth";
import {
  useCentralAuth,
  useIngressClass,
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
