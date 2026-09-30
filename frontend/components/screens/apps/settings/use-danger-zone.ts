"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { recordDeregisterPending } from "@/components/screens/apps/overview/use-deregister-pending";
import type { MutationResult } from "@/graphql/identity/identity.types";
import { DEREGISTER_APP } from "@/graphql/lifecycle/lifecycle.mutations";
import { PREVIEW_DEREGISTER_APP } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftDeregisterPreview } from "@/graphql/lifecycle/lifecycle.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

interface DeregisterResp {
  deregisterAstroliftApp: MutationResult<{
    workflowId: string;
    stillLiveResources: string[];
  }>;
}

interface PreviewResp {
  previewAstroliftDeregister: AstroliftDeregisterPreview | null;
}

/**
 * Hard-deregister + full teardown (#392 + #436 A/B/C). Fires
 * `DeregisterAppWorkflow` with a deterministic workflow id, so re-firing
 * joins the existing run (partial-failure resume is a one-click retry).
 *
 * - A: the authorized preview loads before the trigger badge renders;
 *   `loadPreview` refreshes it when the confirm dialog opens.
 * - B: a clean kickoff records the grace-period entry the
 *   DeregisterPendingBanner consumes, then navigates to /apps.
 * - C: a partial failure returns the still-live resources, kept here so
 *   the retry CTA can re-fire without reopening the dialog.
 */
export function useDangerZone(appSlug: string, appName: string) {
  const t = useTranslations("apps.settings.dangerZone");
  const router = useRouter();
  const [stillLive, setStillLive] = React.useState<string[]>([]);
  const [deregister, { loading }] = useMutation<DeregisterResp>(DEREGISTER_APP);

  const permissions = useMyPermissions();
  const previewEnabled = !!appSlug && !permissions.loading && permissions.can("app.delete");
  // This is a scoped database read; the server still checks the actual app.
  // Load the badge before confirmation, then refresh on each modal open.
  const previewQuery = useQuery<PreviewResp>(PREVIEW_DEREGISTER_APP, {
    variables: { appSlug },
    skip: !previewEnabled,
    fetchPolicy: "cache-and-network",
  });
  const refetchPreview = previewQuery.refetch;
  const loadPreview = React.useCallback(() => {
    if (!previewEnabled) return;
    void refetchPreview().catch((err: unknown) => {
      toast.error(err instanceof Error ? err.message : t("toastFailed"));
    });
  }, [previewEnabled, refetchPreview, t]);

  const preview =
    previewEnabled && !previewQuery.error
      ? (previewQuery.data?.previewAstroliftDeregister ?? null)
      : null;
  const previewLoading = previewQuery.loading && !preview;

  /** Resolves true on a clean kickoff (close the dialog; already navigating). */
  async function onDeregister(confirmName: string): Promise<boolean> {
    try {
      const { data } = await deregister({
        variables: { input: { appSlug, confirmName } },
      });
      const env = data?.deregisterAstroliftApp;
      if (!env) {
        toast.error(t("toastNoResponse"));
        return false;
      }
      if (!env.ok) {
        toast.error(env.errors?.[0]?.message ?? t("toastFailed"));
        return false;
      }
      const payload = env.data;
      if (!payload) {
        toast.error(t("toastNoPayload"));
        return false;
      }
      // Partial-failure resume returns the still-live list; the
      // mutation itself accepts the resume so a retry from this
      // surface is the recovery path. Initial kickoff returns an
      // empty list — happy-path redirect.
      if (payload.stillLiveResources.length > 0) {
        setStillLive(payload.stillLiveResources);
        toast.warning(t("toastResumePending", { count: payload.stillLiveResources.length }));
        return false;
      }
      // Pin the grace-period countdown banner so the operator can
      // cancel within the 5-min window from any app subpage.
      recordDeregisterPending(appSlug, payload.workflowId);
      setStillLive([]);
      toast.success(t("toastKickoff", { workflowId: payload.workflowId }));
      router.push("/apps");
      return true;
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toastFailed"));
      return false;
    }
  }

  // Partial-failure resume (#436 C): re-fires against the same workflow id
  // (Temporal de-dup joins the existing run) without re-opening the modal.
  // The app name passes the backend's confirm-name guard; the operator
  // already typed it once.
  function onRetry(): Promise<boolean> {
    return onDeregister(appName);
  }

  return {
    appName,
    loading,
    preview,
    previewLoading,
    previewError: previewQuery.error,
    stillLive,
    loadPreview,
    onDeregister,
    onRetry,
  };
}
