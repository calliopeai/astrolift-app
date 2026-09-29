"use client";

import { useMutation } from "@apollo/client/react";
import { toast } from "sonner";

import type { MutationResult } from "@/graphql/identity/identity.types";
import { SCALE_WORKLOAD } from "@/graphql/lifecycle/lifecycle.mutations";

/**
 * The inline scale popover's mutation (#668). `apply` validates the typed
 * value, scales, toasts, and resolves true when the popover should close.
 * The data half of ScalePopoverView.
 */
export function useScaleWorkload(workloadId: string, workloadName: string, currentDesired: number) {
  const [scale, { loading }] = useMutation<{
    scaleAstroliftWorkload: MutationResult<unknown>;
  }>(SCALE_WORKLOAD);

  async function apply(value: string): Promise<boolean> {
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
        variables: { input: { workloadId, replicas: next } },
      });
      if (data?.scaleAstroliftWorkload.ok) {
        toast.success(`${workloadName} → ${next} replicas`);
        return true;
      }
      toast.error(data?.scaleAstroliftWorkload.errors?.[0]?.message ?? "Scale failed");
      return false;
    } catch (err) {
      toast.error((err as Error).message);
      return false;
    }
  }

  return { workloadName, currentDesired, loading, apply };
}
