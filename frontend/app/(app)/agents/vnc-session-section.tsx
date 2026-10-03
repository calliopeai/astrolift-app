"use client";

import { VncSessionSectionView } from "@/components/screens/agents/list/VncSessionSectionView";
import { useVncSession } from "@/components/screens/agents/list/use-vnc-session";

/**
 * Spec-level "live session" toggle container: owns the optimistic state and
 * the environment-spec write via useVncSession. Shared by the agent secrets
 * dialog and the agent settings route.
 */
export function VncSessionSection({
  envSpecSlug,
  envSpecId,
  vncEnabled,
}: {
  envSpecSlug: string;
  envSpecId?: string;
  vncEnabled: boolean;
}) {
  return <VncSessionSectionView {...useVncSession(envSpecSlug, vncEnabled, envSpecId)} />;
}
