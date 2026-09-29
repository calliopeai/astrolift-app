"use client";

import { useQuery } from "@apollo/client/react";

import type { CursorPage } from "@/components/data-table";
import { LIST_DEPLOYMENTS_PAGE } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftDeployment } from "@/graphql/lifecycle/lifecycle.types";

import { deployShort } from "./apps-agents-model";
import { HOME_POLL_MS, readState } from "./home-reads";
import type { RecentDeployItem, RecentDeploymentsPanelViewProps } from "./RecentDeploymentsPanel";

interface DeploymentsPageResp {
  astroliftDeploymentsPage: CursorPage<AstroliftDeployment>;
}

/**
 * Five newest with the total. The variables are the Builder layout's
 * Deployments panel's, so the two read one cache entry.
 */
export const RECENT_DEPLOYMENTS_VARIABLES = {
  appSlug: null,
  environmentName: null,
  statuses: null,
  search: null,
  limit: 5,
  after: null,
};

export function recentDeployItem(d: AstroliftDeployment): RecentDeployItem {
  return {
    id: d.id,
    short: deployShort(d),
    app: d.registeredAppSlug,
    environment: d.environmentName || "no environment",
    status: d.status,
    at: d.startedAt ?? d.createdAt,
  };
}

/** Recent deployments' data: one cursor page of five, newest first. */
export function useRecentDeployments(): Omit<RecentDeploymentsPanelViewProps, "panel"> {
  const q = useQuery<DeploymentsPageResp>(LIST_DEPLOYMENTS_PAGE, {
    variables: RECENT_DEPLOYMENTS_VARIABLES,
    fetchPolicy: "cache-and-network",
    pollInterval: HOME_POLL_MS,
  });
  const page = (q.data ?? q.previousData)?.astroliftDeploymentsPage;
  return {
    items: (page?.items ?? []).map(recentDeployItem),
    count: page?.totalCount ?? null,
    ...readState(q, Boolean(page)),
  };
}
