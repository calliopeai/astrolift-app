"use client";

import { useQuery } from "@apollo/client/react";

import { LIST_DEPLOYMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftDeployment } from "@/graphql/lifecycle/lifecycle.types";

interface DeploymentsResp {
  astroliftDeployments: AstroliftDeployment[];
}

/**
 * The freshest deployment for the latest-deployment row, polled every 30s.
 * The data half of LatestDeploymentRow.
 */
export function useLatestDeployment(appSlug: string) {
  // limit=1: we only need the freshest row. The full count powers the
  // QuickLinksGrid's deployments chip via a separate cached query.
  const { data, loading } = useQuery<DeploymentsResp>(LIST_DEPLOYMENTS, {
    variables: { appSlug, limit: 1 },
    fetchPolicy: "cache-and-network",
    pollInterval: 30_000,
  });

  return {
    /** True only for the first load; a poll keeps showing the last row. */
    loading: loading && !data,
    latest: data?.astroliftDeployments?.[0] ?? null,
  };
}
