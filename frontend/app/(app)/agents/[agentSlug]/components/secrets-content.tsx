"use client";

import {
  AgentSecretsTab,
  AgentSecretValues,
} from "@/components/screens/agents/detail/AgentSecretsTab";
import { useAgentSecretValues } from "@/components/screens/agents/detail/use-agent-secrets-tab";
import { useSettingsSection } from "@/components/settings/use-settings-section";

import { AgentSecretBundles } from "../../agent-secret-bundles";

/**
 * The Secrets tab's containers: the env spec's bindings (the spec slug is
 * the agent slug) and its bundles. Only the section on screen mounts, so
 * only its queries run.
 */
export function SecretsContent({ slug }: { slug: string }) {
  return (
    <AgentSecretsTab
      section={useSettingsSection()}
      values={<Values slug={slug} />}
      bundles={<AgentSecretBundles envSpecSlug={slug} active />}
    />
  );
}

function Values({ slug }: { slug: string }) {
  return <AgentSecretValues {...useAgentSecretValues(slug)} />;
}
