"use client";

import { useMutation } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import type { MutationResult } from "@/graphql/identity/identity.types";
import {
  PAUSE_APP_WEBHOOK_DEPLOYS,
  RESUME_APP_WEBHOOK_DEPLOYS,
} from "@/graphql/registry/registry.mutations";
import { GET_APP } from "@/graphql/registry/registry.queries";

interface WebhookDeploysPayload {
  id: string;
  slug: string;
  webhookDeploysPaused: boolean;
  webhookDeploysPausedAt: string | null;
  webhookDeploysPausedByEmail: string | null;
  webhookDeploysPauseReason: string;
}
interface PauseWebhookDeploysResp {
  pauseAstroliftAppWebhookDeploys: MutationResult<WebhookDeploysPayload>;
}
interface ResumeWebhookDeploysResp {
  resumeAstroliftAppWebhookDeploys: MutationResult<WebhookDeploysPayload>;
}

/**
 * App-global webhook-deploy pause (#399). Stops CI / push / scheduled
 * deploys across every environment without taking ingress down; manual
 * deploys keep flowing. Refetches GET_APP so the pause state re-renders.
 */
export function useWebhookDeploysPause(appSlug: string) {
  const t = useTranslations("apps.settings.webhookDeploys");
  const refetch = [{ query: GET_APP, variables: { slug: appSlug } }];
  const [pause, { loading: pausing }] = useMutation<PauseWebhookDeploysResp>(
    PAUSE_APP_WEBHOOK_DEPLOYS,
    { refetchQueries: refetch, awaitRefetchQueries: true }
  );
  const [resume, { loading: resuming }] = useMutation<ResumeWebhookDeploysResp>(
    RESUME_APP_WEBHOOK_DEPLOYS,
    { refetchQueries: refetch, awaitRefetchQueries: true }
  );

  /** Resolves true when the pause landed (close the confirm dialog). */
  async function onPause(reason: string): Promise<boolean> {
    try {
      const { data } = await pause({
        variables: { input: { appSlug, reason: reason.trim() || null } },
      });
      const env = data?.pauseAstroliftAppWebhookDeploys;
      if (!env) {
        toast.error(t("toastNoResponse"));
        return false;
      }
      if (!env.ok) {
        toast.error(env.errors?.[0]?.message ?? t("toastPauseFailed"));
        return false;
      }
      toast.success(t("toastPaused"));
      return true;
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toastPauseFailed"));
      return false;
    }
  }

  async function onResume() {
    try {
      const { data } = await resume({ variables: { input: { appSlug } } });
      const env = data?.resumeAstroliftAppWebhookDeploys;
      if (!env) {
        toast.error(t("toastNoResponse"));
        return;
      }
      if (!env.ok) {
        toast.error(env.errors?.[0]?.message ?? t("toastResumeFailed"));
        return;
      }
      toast.success(t("toastResumed"));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toastResumeFailed"));
    }
  }

  return { pausing, resuming, onPause, onResume };
}
