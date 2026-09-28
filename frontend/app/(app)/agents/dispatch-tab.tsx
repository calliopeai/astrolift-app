"use client";

import { AgentSecretsDialog } from "@/app/(app)/agents/agent-secrets-dialog";
import { DispatchTabView } from "@/components/screens/agents/list/DispatchTab";
import { useDispatchTab } from "@/components/screens/agents/list/use-dispatch-tab";

/**
 * The fleet-level Dispatch tab (#1105). The view owns the markup; the secrets
 * dialog stays a container so its status query runs only for a pinned spec.
 */
export function DispatchTab({ orgId }: { orgId: string }) {
  return (
    <DispatchTabView
      {...useDispatchTab(orgId)}
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
