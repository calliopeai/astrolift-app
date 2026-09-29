"use client";

import { useLazyQuery, useMutation } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import type { MutationResult } from "@/graphql/identity/identity.types";
import { FORCE_REDEPLOY } from "@/graphql/lifecycle/lifecycle.mutations";
import { PREVIEW_FORCE_REDEPLOY } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftForceRedeployPreview } from "@/graphql/lifecycle/lifecycle.types";

interface ForceRedeployResp {
  forceAstroliftRedeploy: MutationResult<{
    deploymentsCancelled: number;
    k8sObjectsDeleted: number;
    workflowDispatched: boolean;
    runUrl: string | null;
    dispatchMessage: string | null;
  }>;
}

interface ForceRedeployPreviewResp {
  previewAstroliftForceRedeploy: AstroliftForceRedeployPreview | null;
}

/**
 * Destructive recovery for wedged apps (#389 + #436 D): cancel in-flight
 * Deployment rows, delete the per-workload k8s objects, re-dispatch the
 * deploy CI workflow. `loadPreview` fetches the in-flight deployments the
 * recovery will fail; the view calls it when its confirm dialog opens.
 */
export function useForceRedeploy(appSlug: string) {
  const [forceRedeploy, { loading }] = useMutation<ForceRedeployResp>(FORCE_REDEPLOY);

  // ``cache-and-network`` keeps the list fresh between reopens — a deploy
  // that landed since the previous open shouldn't be invisible.
  const [runPreview, previewQuery] = useLazyQuery<ForceRedeployPreviewResp>(
    PREVIEW_FORCE_REDEPLOY,
    {
      fetchPolicy: "cache-and-network",
    }
  );
  const loadPreview = React.useCallback(() => {
    void runPreview({ variables: { appSlug } });
  }, [runPreview, appSlug]);

  // Coerce out of useLazyQuery's DeepPartial<TData> envelope once the
  // top-level field is present — children below all live on the same
  // resolver, so either the whole shape lands or `data` is undefined.
  const preview =
    (previewQuery.data?.previewAstroliftForceRedeploy as
      | AstroliftForceRedeployPreview
      | undefined) ?? null;
  const previewLoading = previewQuery.loading && !preview;

  /** Resolves true when the recovery ran (close the confirm dialog). */
  async function onForceRedeploy(confirmSlug: string): Promise<boolean> {
    try {
      const { data } = await forceRedeploy({
        variables: { input: { appSlug, confirmSlug } },
      });
      const env = data?.forceAstroliftRedeploy;
      if (!env) {
        toast.error("Force redeploy failed: no response from backend.");
        return false;
      }
      if (!env.ok) {
        toast.error(env.errors?.[0]?.message ?? "Force redeploy failed.");
        return false;
      }
      const payload = env.data;
      if (!payload) {
        toast.error("Force redeploy returned no payload.");
        return false;
      }
      const counts =
        `Cancelled ${payload.deploymentsCancelled} deploy(s), deleted ${payload.k8sObjectsDeleted}` +
        " k8s object(s).";
      if (payload.workflowDispatched) {
        const tail = payload.runUrl ? ` Watch run: ${payload.runUrl}` : "";
        toast.success(`${counts} Workflow dispatched.${tail}`, { duration: 8000 });
      } else {
        // Partial success — cancellation + delete still landed even
        // though the CI dispatch failed. The operator can re-fire the
        // dispatch via the CI-setup section once the host is reachable.
        toast.warning(
          `${counts} Workflow dispatch failed: ${payload.dispatchMessage ?? "unknown error"}`,
          { duration: 10000 }
        );
      }
      return true;
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Force redeploy failed.");
      return false;
    }
  }

  return { appSlug, loading, preview, previewLoading, loadPreview, onForceRedeploy };
}
