"use client";

import { useQuery } from "@apollo/client/react";

import { LIST_DEPLOYMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftDeployment } from "@/graphql/lifecycle/lifecycle.types";

interface DeploymentsResp {
  astroliftDeployments: AstroliftDeployment[];
}

/**
 * How many recent deploys the app frame and its Overview read. One number
 * for every reader, so the frame's Deploy, the latest-deploy panel, the
 * approval queue and the deploy strip are one query in the cache, not five
 * (Leo's page rule 2). Twenty-five covers the strip's twenty, the queue and
 * the last-known-good a rollback targets.
 */
export const APP_RECENT_DEPLOYS = 25;

/** The variables of that one read; mutations refetch it by these. */
export function appDeploysVariables(appSlug: string) {
  return { appSlug, limit: APP_RECENT_DEPLOYS };
}

/**
 * The app's recent deploys, newest first: the one `LIST_DEPLOYMENTS` read
 * the frame and the Overview share. Readers mount it cache-first, so a
 * second reader on the page is served from the cache; `live` makes this
 * reader the one that keeps it fresh (one poll per page, not one per panel).
 */
export function useAppDeploys(appSlug: string, { live = false }: { live?: boolean } = {}) {
  const q = useQuery<DeploymentsResp>(LIST_DEPLOYMENTS, {
    variables: appDeploysVariables(appSlug),
    fetchPolicy: live ? "cache-and-network" : "cache-first",
    pollInterval: live ? 15_000 : undefined,
  });
  return {
    deployments: q.data?.astroliftDeployments ?? [],
    loading: q.loading && !q.data,
    error: q.error && !q.data ? q.error.message : null,
    refetch: q.refetch,
  };
}
