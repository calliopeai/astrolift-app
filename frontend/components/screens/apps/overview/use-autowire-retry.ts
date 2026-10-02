"use client";

import { useMutation } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import type { MutationResult } from "@/graphql/identity/identity.types";
import { RETRY_ASTROLIFT_AUTOWIRE } from "@/graphql/lifecycle/lifecycle.mutations";
import { GET_APP } from "@/graphql/registry/registry.queries";

interface RetryResp {
  retryAstroliftAutowire: MutationResult<{
    connected: boolean;
    allOk: boolean;
    ciWorkflow: string;
    webhook: string;
    secrets: string;
    detail: string;
  }>;
}

/**
 * Re-runs the autowire chain (#1108) and refetches the app so the banner
 * reflects the new step states. The data half of AutowireStatusBannerView.
 */
export function useAutowireRetry(appSlug: string) {
  const t = useTranslations("apps.overview.autowire.feedback");
  const [retry, { loading }] = useMutation<RetryResp>(RETRY_ASTROLIFT_AUTOWIRE, {
    refetchQueries: [{ query: GET_APP, variables: { slug: appSlug, includeDrift: true } }],
    awaitRefetchQueries: true,
  });

  async function onRetry() {
    try {
      const { data } = await retry({ variables: { input: { appSlug } } });
      const payload = data?.retryAstroliftAutowire;
      if (!payload?.ok) {
        toast.error(payload?.errors?.[0]?.message ?? t("failed"));
        return;
      }
      if (payload.data?.allOk) {
        toast.success(t("wired"));
      } else if (!payload.data?.connected) {
        toast.message(t("connect"));
      } else {
        toast.warning(payload.data?.detail || t("attention"));
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("failed"));
    }
  }

  return { retrying: loading, onRetry };
}
