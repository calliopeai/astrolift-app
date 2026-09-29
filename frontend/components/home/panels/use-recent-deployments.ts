"use client";

import type { AstroliftDeployment } from "@/graphql/lifecycle/lifecycle.types";

import { deployShort } from "./apps-agents-model";
import { useHomeDeployments } from "./home-reads";
import type { RecentDeployItem, RecentDeploymentsPanelViewProps } from "./RecentDeploymentsPanel";

/** The deploys the panel shows. */
export const RECENT_DEPLOYMENTS_SHOWN = 5;

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

/**
 * The count Recent deployments shows: exact while the shared read holds
 * every deploy, none once it is full, since the read cannot say how many
 * lie past it.
 */
export function recentDeploymentsCount(read: {
  deployments: readonly unknown[];
  capped: boolean;
}): number | null {
  return read.capped ? null : read.deployments.length;
}

/**
 * Recent deployments' data: the five newest of Home's shared deployments
 * read (home-reads.ts), the one Waiting on you and Failing make, so the
 * three panels fetch deployments once between them.
 */
export function useRecentDeployments(): Omit<RecentDeploymentsPanelViewProps, "panel"> {
  const { deployments, capped, ...read } = useHomeDeployments();
  return {
    items: deployments.slice(0, RECENT_DEPLOYMENTS_SHOWN).map(recentDeployItem),
    count: recentDeploymentsCount({ deployments, capped }),
    ...read,
  };
}
