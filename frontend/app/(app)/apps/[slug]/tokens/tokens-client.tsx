"use client";

import { DeployTokensScreen } from "@/components/screens/apps/secrets/DeployTokensScreen";
import { useDeployTokens } from "@/components/screens/apps/secrets/use-deploy-tokens";

import { AppTabs } from "../components/app-tabs";

/** App Deploy tokens tab: calls the hook, renders the screen. */
export function AppDeployTokensClient({ slug }: { slug: string }) {
  return (
    <DeployTokensScreen
      {...useDeployTokens(slug)}
      tabs={<AppTabs slug={slug} active="secrets" />}
    />
  );
}
