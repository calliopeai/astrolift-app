"use client";

import { SecretHistoryPanelView } from "@/components/screens/apps/secrets/SecretHistoryPanel";
import { SecretsScreen } from "@/components/screens/apps/secrets/SecretsScreen";
import { useAppSecrets } from "@/components/screens/apps/secrets/use-app-secrets";
import { useSecretHistory } from "@/components/screens/apps/secrets/use-secret-history";

import { AppTabs } from "../components/app-tabs";
import { PushAndRotateButton } from "../components/ci-setup-section";

/**
 * App Secrets tab. The screen owns the markup; the history popover gets a
 * container here so its query runs only while it is open.
 */
export function SecretsClient({ slug }: { slug: string }) {
  return (
    <SecretsScreen
      {...useAppSecrets(slug)}
      tabs={<AppTabs slug={slug} active="secrets" />}
      pushToGitHub={<PushAndRotateButton appSlug={slug} variant="outline" label="Push to GitHub" />}
      renderHistory={(secretKey) => <SecretHistory appSlug={slug} secretKey={secretKey} />}
    />
  );
}

function SecretHistory({ appSlug, secretKey }: { appSlug: string; secretKey: string }) {
  return <SecretHistoryPanelView {...useSecretHistory(appSlug, secretKey)} />;
}
