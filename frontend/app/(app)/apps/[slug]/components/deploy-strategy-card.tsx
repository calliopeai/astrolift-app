"use client";

import { DeployStrategyCardView } from "@/components/screens/apps/overview/DeployStrategyCard";
import { useDeployStrategy } from "@/components/screens/apps/overview/use-deploy-strategy";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

/** Deploy strategy card: the hook owns the save, the view owns the markup. */
export function DeployStrategyCard({ app }: { app: AstroliftRegisteredApp }) {
  return <DeployStrategyCardView {...useDeployStrategy(app)} />;
}
