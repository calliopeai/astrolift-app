"use client";

import { useQuery } from "@apollo/client/react";

import { appTopology } from "@/components/topology";
import { GET_APP, LIST_WORKLOADS } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp, AstroliftWorkload } from "@/graphql/registry/registry.types";
import { LIST_MANAGED_SERVICES } from "@/graphql/services/services.queries";

interface AppResp {
  astroliftApp: AstroliftRegisteredApp | null;
}
interface WorkloadsResp {
  astroliftWorkloads: AstroliftWorkload[];
}
interface ManagedServicesResp {
  astroliftManagedServices: Array<{
    id: string;
    name: string;
    kind: string;
    variant: string | null;
    status: string;
    environmentName: string;
  }>;
}

/** App > Topology tab data: the app, its workloads and managed services, as a graph. */
export function useAppTopology(slug: string) {
  const app = useQuery<AppResp>(GET_APP, { variables: { slug } });
  const workloads = useQuery<WorkloadsResp>(LIST_WORKLOADS, {
    variables: { appSlug: slug },
  });
  // #727 — fetch managed-service bindings so the topology renders
  // RDS / S3 / SES / etc nodes hanging off the workload layer.
  const managedServices = useQuery<ManagedServicesResp>(LIST_MANAGED_SERVICES, {
    variables: { appSlug: slug },
  });

  const a = app.data?.astroliftApp ?? null;
  const wlList = workloads.data?.astroliftWorkloads ?? [];
  const msList = managedServices.data?.astroliftManagedServices ?? [];
  const { nodes, edges } = a ? appTopology(a, wlList, msList) : { nodes: [], edges: [] };

  return {
    slug,
    app: a,
    loading: app.loading && !app.data,
    workloadsLoading: workloads.loading,
    nodes,
    edges,
  };
}
