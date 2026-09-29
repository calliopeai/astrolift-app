"use client";

import { useAppDeploys } from "./use-app-deploys";

/**
 * The last `limit` deploys behind the deploy activity strip. The data half
 * of DeployActivityStrip. It reads the app frame's deploys (use-app-deploys),
 * which the latest-deploy panel keeps live, so the strip adds no query.
 */
export function useDeployActivity(appSlug: string, limit = 20) {
  const { deployments, loading } = useAppDeploys(appSlug);
  return { deployments: deployments.slice(0, limit), loading, limit };
}
