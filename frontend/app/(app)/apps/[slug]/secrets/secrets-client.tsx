"use client";

import { SecretHistoryPanelView } from "@/components/screens/apps/secrets/SecretHistoryPanel";
import type { SecretsSection } from "@/components/screens/apps/secrets/secrets-list";
import { SecretsScreen } from "@/components/screens/apps/secrets/SecretsScreen";
import { useAppSecrets } from "@/components/screens/apps/secrets/use-app-secrets";
import { useSecretHistory } from "@/components/screens/apps/secrets/use-secret-history";

import { appPath, useAppChrome } from "@/lib/app-chrome-context";
import { AppTabs } from "../components/app-tabs";
import { PushAndRotateButton } from "../components/ci-setup-section";

/**
 * App Secrets tab. The screen owns the markup; the history sheet gets a
 * container here so its query runs only while it is open.
 */
export function SecretsClient({ slug, section }: { slug: string; section: SecretsSection }) {
  const chrome = useAppChrome();
  const base = appPath(chrome, slug, "secrets");
  return (
    <SecretsScreen
      {...useAppSecrets(slug, section)}
      tabs={<AppTabs slug={slug} active="secrets" />}
      pushToGitHub={<PushAndRotateButton appSlug={slug} variant="outline" label="Push to GitHub" />}
      renderHistory={(secretKey) => <SecretHistory appSlug={slug} secretKey={secretKey} />}
      sectionHref={(s) => (s === "bundles" ? `${base}?section=bundles` : base)}
    />
  );
}

function SecretHistory({ appSlug, secretKey }: { appSlug: string; secretKey: string }) {
  return <SecretHistoryPanelView {...useSecretHistory(appSlug, secretKey)} />;
}
