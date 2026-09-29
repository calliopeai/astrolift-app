"use client";

import { ProvisioningProgressView } from "@/components/screens/apps/overview/ProvisioningProgress";
import { useProvisioningProgress } from "@/components/screens/apps/overview/use-provisioning-progress";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

/** Provisioning step list, polling while the app provisions. */
export function ProvisioningProgressPanel({ app }: { app: AstroliftRegisteredApp }) {
  return <ProvisioningProgressView {...useProvisioningProgress(app)} />;
}
