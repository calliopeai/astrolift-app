"use client";

import { useQuery } from "@apollo/client/react";

import { LIST_DEPLOYMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftDeployment } from "@/graphql/lifecycle/lifecycle.types";

interface DeploymentsResp {
  astroliftDeployments: AstroliftDeployment[];
}

/**
 * The deployment count behind the Deployments quick-link chip. The data
 * half of QuickLinksGrid.
 */
export function useQuickLinks(appSlug: string) {
  // No environmentName / limit: we want the full count for this app.
  // The query is cached, so the deployments tab benefits from the
  // warmed cache when the operator clicks through.
  const { data, loading } = useQuery<DeploymentsResp>(LIST_DEPLOYMENTS, {
    variables: { appSlug },
    fetchPolicy: "cache-and-network",
  });

  return { count: data?.astroliftDeployments?.length ?? 0, loading };
}
