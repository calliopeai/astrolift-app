"use client";

import { useMutation } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import type { MutationResult } from "@/graphql/identity/identity.types";
import { FORCE_REDEPLOY } from "@/graphql/lifecycle/lifecycle.mutations";
import { GET_APP } from "@/graphql/registry/registry.queries";

interface ForceRedeployResp {
  forceAstroliftRedeploy: MutationResult<{
    deploymentsCancelled: number;
    k8sObjectsDeleted: number;
    workflowDispatched: boolean;
    runUrl: string | null;
    dispatchMessage: string | null;
  }>;
}

/**
 * Fires FORCE_REDEPLOY (#389) with `confirmSlug = appSlug`. The data half of
 * ReprovisionCalloutView (#407 A). `onConfirm` throws on failure so the
 * ConfirmDialog stays open and surfaces the error; it resolves on success.
 */
export function useForceReprovision(appSlug: string) {
  const t = useTranslations("apps.detail.reprovision");
  const [forceRedeploy, { loading }] = useMutation<ForceRedeployResp>(FORCE_REDEPLOY, {
    refetchQueries: [{ query: GET_APP, variables: { slug: appSlug, includeDrift: true } }],
    awaitRefetchQueries: true,
  });

  async function onConfirm() {
    const { data } = await forceRedeploy({
      variables: {
        input: { appSlug, confirmSlug: appSlug },
      },
    });
    const payload = data?.forceAstroliftRedeploy;
    if (!payload) {
      throw new Error(t("error.noResponse"));
    }
    if (!payload.ok) {
      throw new Error(payload.errors?.[0]?.message ?? t("error.generic"));
    }
    const inner = payload.data;
    if (inner?.workflowDispatched) {
      toast.success(t("toast.dispatched"));
    } else {
      // Partial success — the cancellation + delete passes still ran,
      // but the CI workflow-dispatch failed. Surface the message so
      // the operator knows what to retry.
      toast.warning(t("toast.partial", { message: inner?.dispatchMessage ?? "" }));
    }
  }

  return { appSlug, redeploying: loading, onConfirm };
}
