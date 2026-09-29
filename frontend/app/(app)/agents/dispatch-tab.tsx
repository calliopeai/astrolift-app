"use client";

import { AgentSecretsDialog } from "@/app/(app)/agents/agent-secrets-dialog";
import { DispatchTabView } from "@/components/screens/agents/list/DispatchTab";
import { useDispatchTab } from "@/components/screens/agents/list/use-dispatch-tab";

/**
 * The Run agent form (#1105, the old fleet Dispatch tab). The view owns the
 * markup; the secrets dialog stays a container so its status query runs only
 * for a pinned spec.
 */
export function DispatchTab({
  orgId,
  defaultAgentSlug = "",
}: {
  orgId: string;
  /** The agent picked on arrival (`?agent=` on Run agent). */
  defaultAgentSlug?: string;
}) {
  return (
    <DispatchTabView
      {...useDispatchTab(orgId)}
      defaultAgentSlug={defaultAgentSlug}
      renderSecretsDialog={({ spec, open, onOpenChange }) => (
        <AgentSecretsDialog
          envSpecSlug={spec.slug}
          envSpecName={spec.name}
          managedModel={spec.managedModel}
          vncEnabled={spec.vncEnabled}
          open={open}
          onOpenChange={onOpenChange}
        />
      )}
    />
  );
}
