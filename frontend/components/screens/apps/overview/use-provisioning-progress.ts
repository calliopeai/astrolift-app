"use client";

import { useQuery } from "@apollo/client/react";

import { GET_APP } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

/**
 * Polls the app every 3s while it is provisioning and returns the freshest
 * provisioning progress (the passed app's until the first poll lands). The
 * data half of ProvisioningProgressView.
 */
export function useProvisioningProgress(app: AstroliftRegisteredApp) {
  const isProvisioning = app.provisioningStatus === "provisioning";

  const { data } = useQuery<{ astroliftApp: AstroliftRegisteredApp }>(GET_APP, {
    variables: { slug: app.slug, includeDrift: false },
    skip: !isProvisioning,
    pollInterval: 3000,
    fetchPolicy: "network-only",
  });

  const liveApp = data?.astroliftApp ?? app;
  return { isProvisioning, progress: liveApp.provisioningProgress };
}
