"use client";

import { useQuery } from "@apollo/client/react";

import { LIST_DEPLOYMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftDeployment } from "@/graphql/lifecycle/lifecycle.types";

interface DeploymentsResp {
  astroliftDeployments: AstroliftDeployment[];
}

/**
 * The last `limit` deploys behind the deploy activity strip, polled every
 * 15s. The data half of DeployActivityStrip.
 */
export function useDeployActivity(appSlug: string, limit = 20) {
  const { data, loading } = useQuery<DeploymentsResp>(LIST_DEPLOYMENTS, {
    variables: { appSlug, limit },
    fetchPolicy: "cache-and-network",
    pollInterval: 15_000,
  });

  const deployments = (data?.astroliftDeployments ?? []) as AstroliftDeployment[];
  return { deployments, loading, limit };
}
