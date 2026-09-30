"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { SCALE_WORKLOAD } from "@/graphql/lifecycle/lifecycle.mutations";
import type { AstroliftActionPermission } from "@/graphql/__generated__/schema";
import {
  useWorkloadActionPermission,
  workloadActionRefetch,
} from "./use-workload-action-permission";
import { GET_WORKLOAD_SCALING_STATUS } from "@/graphql/registry/registry.queries";

// 5s poll matches the issue spec — short enough to make the
// 'Scaling…' indicator feel live, long enough that we're not
// hammering kube-apiserver via the get_workload_status driver call.
const SCALING_POLL_MS = 5_000;

export interface ScalingStatus {
  hpaEnabled: boolean;
  hpaMinReplicas: number | null;
  hpaMaxReplicas: number | null;
  hpaTargetCpuPct: number;
  currentReplicas: number;
  desiredReplicas: number;
  isScaling: boolean;
  replicaLowerBound: number;
  replicaUpperBound: number;
  sourcedAt: string;
}

interface ScalingResp {
  astroliftWorkloadScalingStatus: ScalingStatus | null;
}

interface ScaleMutationResp {
  scaleAstroliftWorkload: {
    ok: boolean;
    errors: Array<{ code: string; message: string; field: string | null }>;
    data: {
      workloadId: string;
      desiredReplicas: number | null;
      readyReplicas: number | null;
    } | null;
  };
}

/**
 * Live scaling status for one workload and the manual scale mutation. The
 * data half of ScalingCardView.
 */
export function useWorkloadScaling({
  workloadId,
  appSlug,
  workloadSlug,
  environmentName,
  permission,
  version,
}: {
  workloadId: string;
  appSlug: string;
  workloadSlug: string;
  environmentName: string | null;
  permission: AstroliftActionPermission | undefined;
  version: number | undefined;
}) {
  const actions = useTranslations("apps.workloadActions");
  const feedback = useWorkloadActionPermission(
    environmentName === null
      ? permission
      : { allowed: false, code: "PRECONDITION", reason: actions("selectedEnvironmentUnsupported") },
    version
  );
  const t = useTranslations("apps.workloadDetail");
  const { data, loading } = useQuery<ScalingResp>(GET_WORKLOAD_SCALING_STATUS, {
    variables: { appSlug, workloadSlug, environmentName },
    pollInterval: SCALING_POLL_MS,
    fetchPolicy: "cache-and-network",
  });

  const [scale, { loading: scaling }] = useMutation<ScaleMutationResp>(
    SCALE_WORKLOAD,
    workloadActionRefetch
  );

  /**
   * Resolves false when the server rejected the scale, so the view can
   * revert its slider to the server's desired count.
   */
  async function onApply(pending: number): Promise<boolean> {
    if (feedback.blocked()) return false;
    try {
      const { data: resp } = await scale({
        variables: { input: { workloadId, replicas: pending }, ifMatchVersion: version },
      });
      const payload = resp?.scaleAstroliftWorkload;
      if (payload?.ok) {
        const requested = payload.data?.desiredReplicas ?? pending;
        toast.success(t("scaling.successToast").replace("{replicas}", String(requested)));
        return true;
      }
      return feedback.reject(
        payload,
        t("scaling.errorToast").replace("{message}", "unknown error")
      );
    } catch (err) {
      toast.error(
        err instanceof Error
          ? err.message
          : t("scaling.errorToast").replace("{message}", "unknown error")
      );
      return false;
    }
  }

  return {
    status: data?.astroliftWorkloadScalingStatus ?? null,
    loading,
    scaling,
    canDeploy: feedback.permission.allowed,
    permissionReason: feedback.permission.reason,
    onApply,
  };
}
