"use client";

import {
  CiSetupSectionView,
  PushAndRotateButtonView,
  type PushAndRotateButtonViewProps,
} from "@/components/screens/apps/overview/CiSetupSection";
import { useCiSetup, usePushAndRotate } from "@/components/screens/apps/overview/use-ci-setup";
import type { AstroliftCiWorkflowSyncStatus } from "@/graphql/__generated__/schema";

import { appPath, useAppChrome } from "@/lib/app-chrome-context";

interface Props {
  /** Public app GUID used by managed-workflow drift mutations. */
  appId: string;
  appSlug: string;
  registryUri: string;
  pushCredentialRef: string;
  providerPluginSlug: string;
  deployBranch?: string | null;
  sourceWebhookInstalledAt: string | null;
  ciWorkflowSyncStatus: AstroliftCiWorkflowSyncStatus | null;
  agentMode?: boolean;
}

/**
 * CI setup section on the consolidated Settings page (#382, #854). The
 * view lives in components/screens/apps/overview/CiSetupSection; this
 * container runs its hook and resolves the chrome-aware tokens link.
 */
export function CiSetupSection({ appId, agentMode = false, ...props }: Props) {
  const chrome = useAppChrome();
  const ci = useCiSetup({ appId, appSlug: props.appSlug, agentMode });
  return (
    <CiSetupSectionView
      {...props}
      {...ci}
      agentMode={agentMode}
      tokensHref={appPath(chrome, props.appSlug, "tokens")}
    />
  );
}

/** "Push & rotate" button, shared with the Secrets tab (#681). */
export function PushAndRotateButton({
  appSlug,
  ...props
}: { appSlug: string } & Omit<PushAndRotateButtonViewProps, "pushing" | "onPushAndRotate">) {
  return <PushAndRotateButtonView {...props} {...usePushAndRotate(appSlug)} />;
}
