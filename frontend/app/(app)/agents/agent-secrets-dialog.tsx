"use client";

import { AgentSecretsView } from "@/components/screens/agents/list/AgentSecrets";
import { useAgentSecrets } from "@/components/screens/agents/list/use-agent-secrets";

import { AgentSecretBundles } from "./agent-secret-bundles";
import { ManagedModelSection } from "./managed-model-section";
import { VncSessionSection } from "./vnc-session-section";

/**
 * Manage the VALUES behind an env spec's secret refs (#1173), as a dialog or
 * embedded on the agent Secrets route. The view owns the markup; the bundles
 * card and the spec-level settings keep their own containers.
 */
export function AgentSecretsDialog({
  envSpecSlug,
  envSpecId,
  envSpecName,
  managedModel,
  vncEnabled,
  open,
  onOpenChange,
  embedded = false,
  showRuntimeSettings = true,
}: {
  envSpecSlug: string;
  envSpecId?: string;
  envSpecName: string;
  managedModel: boolean;
  vncEnabled: boolean;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  embedded?: boolean;
  showRuntimeSettings?: boolean;
}) {
  return (
    <AgentSecretsView
      {...useAgentSecrets(envSpecSlug, open)}
      envSpecName={envSpecName}
      open={open}
      onOpenChange={onOpenChange}
      embedded={embedded}
      runtimeSettings={
        showRuntimeSettings ? (
          <>
            {/* Model source — a spec-level property, independent of secret refs. */}
            <ManagedModelSection
              envSpecSlug={envSpecSlug}
              envSpecId={envSpecId}
              managedModel={managedModel}
            />
            {/* Live VNC session is another independent spec-level property. */}
            <VncSessionSection
              envSpecSlug={envSpecSlug}
              envSpecId={envSpecId}
              vncEnabled={vncEnabled}
            />
          </>
        ) : undefined
      }
      bundles={<AgentSecretBundles envSpecSlug={envSpecSlug} active={Boolean(open)} />}
    />
  );
}
