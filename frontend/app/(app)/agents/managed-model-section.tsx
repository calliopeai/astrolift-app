"use client";

import { ManagedModelSectionView } from "@/components/screens/agents/list/ManagedModelSectionView";
import { useManagedModel } from "@/components/screens/agents/list/use-managed-model";

/**
 * Spec-level "model source" toggle container (#1173): owns the optimistic
 * state and the environment-spec write via useManagedModel. Shared by the
 * agent secrets dialog and the agent settings route.
 */
export function ManagedModelSection({
  envSpecSlug,
  envSpecId,
  managedModel,
}: {
  envSpecSlug: string;
  envSpecId?: string;
  managedModel: boolean;
}) {
  return <ManagedModelSectionView {...useManagedModel(envSpecSlug, managedModel, envSpecId)} />;
}
