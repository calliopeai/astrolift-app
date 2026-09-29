"use client";

import { useQuery } from "@apollo/client/react";

import type { AstroliftAppEnvironment } from "@/graphql/__generated__/schema";
import { LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import { LIST_WORKLOADS } from "@/graphql/registry/registry.queries";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";

import type { MetricScopeOptions } from "./MetricScopePicker";

interface EnvResp {
  astroliftEnvironments: AstroliftAppEnvironment[];
}

interface WorkloadResp {
  astroliftWorkloads: AstroliftWorkload[];
}

/** An app's environments and workloads for the metric scope: the data half of MetricScopePicker. */
export function useMetricScopeOptions(appSlug: string): MetricScopeOptions {
  const envs = useQuery<EnvResp>(LIST_ENVIRONMENTS, {
    variables: { appSlug },
    fetchPolicy: "cache-and-network",
  });
  const workloads = useQuery<WorkloadResp>(LIST_WORKLOADS, {
    variables: { appSlug },
    fetchPolicy: "cache-and-network",
  });

  return {
    environments: envs.data?.astroliftEnvironments ?? [],
    environmentsLoading: envs.loading,
    workloads: workloads.data?.astroliftWorkloads ?? [],
    workloadsLoading: workloads.loading,
  };
}
