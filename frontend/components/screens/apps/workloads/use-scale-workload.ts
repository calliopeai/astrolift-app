"use client";

import { useMutation } from "@apollo/client/react";
import { toast } from "sonner";

import type { MutationResult } from "@/graphql/identity/identity.types";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";
import {
  useWorkloadActionPermission,
  workloadActionRefetch,
} from "./use-workload-action-permission";
import { SCALE_WORKLOAD } from "@/graphql/lifecycle/lifecycle.mutations";

/**
 * The inline scale popover's mutation (#668). `apply` validates the typed
 * value, scales, toasts, and resolves true when the popover should close.
 * The data half of ScalePopoverView.
 */
export function useScaleWorkload(workload: AstroliftWorkload, currentDesired: number) {
  const feedback = useWorkloadActionPermission(workload.viewerCan?.scale, workload.version);
  const [scale, { loading }] = useMutation<{
    scaleAstroliftWorkload: MutationResult<unknown>;
  }>(SCALE_WORKLOAD, workloadActionRefetch);

  async function apply(value: string): Promise<boolean> {
    if (feedback.blocked()) return false;
    const next = Number.parseInt(value, 10);
    if (Number.isNaN(next) || next < 0 || next > 50) {
      toast.error("Replicas must be a number between 0 and 50");
      return false;
    }
    if (next === currentDesired) {
      return true;
    }
    try {
      const { data } = await scale({
        variables: {
          input: { workloadId: workload.id, replicas: next },
          ifMatchVersion: workload.version,
        },
      });
      if (data?.scaleAstroliftWorkload.ok) {
        toast.success(`${workload.name} → ${next} replicas`);
        return true;
      }
      return feedback.reject(data?.scaleAstroliftWorkload, "Scale failed");
    } catch (err) {
      toast.error((err as Error).message);
      return false;
    }
  }

  return {
    workloadName: workload.name,
    currentDesired,
    loading,
    apply,
    permission: feedback.permission,
  };
}
