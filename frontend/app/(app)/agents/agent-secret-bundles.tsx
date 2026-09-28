"use client";

import { AgentSecretBundlesView } from "@/components/screens/agents/list/AgentSecretBundles";
import { useAgentSecretBundles } from "@/components/screens/agents/list/use-agent-secret-bundles";

/** Reusable secret bundles of an env spec; queries run only while `active`. */
export function AgentSecretBundles({
  envSpecSlug,
  active,
}: {
  envSpecSlug: string;
  active: boolean;
}) {
  return <AgentSecretBundlesView {...useAgentSecretBundles(envSpecSlug, active)} />;
}
