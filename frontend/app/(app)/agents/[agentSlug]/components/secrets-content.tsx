"use client";

import {
  AgentSecretsTab,
  AgentSecretValues,
} from "@/components/screens/agents/detail/AgentSecretsTab";
import { useAgentSecretValues } from "@/components/screens/agents/detail/use-agent-secrets-tab";
import { useSettingsSection } from "@/components/settings/use-settings-section";
import {
  AgentSecretBundleReadDenied,
  AgentSecretSource,
} from "@/components/screens/agents/detail/AgentSecretSource";
import { useAgentSecretSource } from "@/components/screens/agents/detail/use-agent-secret-source";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";

import { AgentSecretBundles } from "../../agent-secret-bundles";

/**
 * Explicit environment-recipe secrets. No agent-to-recipe default is inferred.
 * Only a confirmed visible recipe mounts values/bundles, one section at a time.
 */
export function SecretsContent({ slug }: { slug: string }) {
  const { org } = useActiveOrg();
  return <ScopedSecretsContent key={`${org?.id ?? ""}:${slug}`} slug={slug} />;
}

function ScopedSecretsContent({ slug }: { slug: string }) {
  const { source, confirmedSpec, editorKey, canReadSecretBundles } = useAgentSecretSource(slug);
  const section = useSettingsSection();
  return (
    <AgentSecretSource {...source}>
      {confirmedSpec && source.canListSecrets && (
        <AgentSecretsTab
          key={editorKey}
          section={section}
          values={<Values slug={confirmedSpec.slug} />}
          bundles={
            canReadSecretBundles ? (
              <AgentSecretBundles envSpecSlug={confirmedSpec.slug} active />
            ) : (
              <AgentSecretBundleReadDenied />
            )
          }
        />
      )}
    </AgentSecretSource>
  );
}

function Values({ slug }: { slug: string }) {
  return <AgentSecretValues {...useAgentSecretValues(slug)} />;
}
