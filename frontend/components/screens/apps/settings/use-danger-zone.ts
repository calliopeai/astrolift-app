"use client";

import { useLazyQuery, useMutation } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { recordDeregisterPending } from "@/components/screens/apps/overview/use-deregister-pending";
import type { MutationResult } from "@/graphql/identity/identity.types";
import { DEREGISTER_APP } from "@/graphql/lifecycle/lifecycle.mutations";
import { PREVIEW_DEREGISTER_APP } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftDeregisterPreview } from "@/graphql/lifecycle/lifecycle.types";

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
 * - A: `loadPreview` fetches the blast-radius preview; the view calls it
 *   when the confirm dialog opens.
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

  // ``cache-and-network`` keeps the count badge fresh whenever the modal
  // reopens — the resource list can change between attempts (operator
  // created/deleted services in a sibling tab) and a stale badge would
  // mislead.
  const [runPreview, previewQuery] = useLazyQuery<PreviewResp>(PREVIEW_DEREGISTER_APP, {
    fetchPolicy: "cache-and-network",
  });
  const loadPreview = React.useCallback(() => {
    void runPreview({ variables: { appSlug } });
  }, [runPreview, appSlug]);

  // useLazyQuery returns a DeepPartial<TData> on `data` to model the
  // "haven't fired yet" state; coerce to the full type once we've
  // checked the top-level field is present.
  const preview =
    (previewQuery.data?.previewAstroliftDeregister as AstroliftDeregisterPreview | undefined) ??
    null;
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
    stillLive,
    loadPreview,
    onDeregister,
    onRetry,
  };
}
