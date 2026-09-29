"use client";

import { useMutation } from "@apollo/client/react";
import { toast } from "sonner";

import { UPDATE_MANAGED_SERVICE } from "@/graphql/services/services.mutations";

// UPDATE_MANAGED_SERVICE interpolates its field list, so codegen leaves it
// untyped; these are the only fields read here.
interface UpdateResult {
  updateManagedService: { ok: boolean; errors?: { message: string }[] | null };
}

export function replicasOf(config: Record<string, unknown>): number {
  const value = Number(config.replicas ?? 1);
  return Number.isFinite(value) ? value : 1;
}

/**
 * Stop, start and scale a hosted vLLM model (#2040). ``replicas`` is one of
 * the driver's in-place fields, so this is an update, never a reprovision;
 * the whole config goes back with only ``replicas`` changed. The data half
 * of ModelReplicasView.
 */
export function useModelReplicas({
  id,
  name,
  config,
  onChanged,
}: {
  id: string;
  name: string;
  config: Record<string, unknown>;
  onChanged: () => void;
}) {
  const [update, { loading }] = useMutation<UpdateResult>(UPDATE_MANAGED_SERVICE);

  async function setReplicas(next: number) {
    const { data } = await update({
      variables: { input: { id, config: { ...config, replicas: next } } },
    });
    const result = data?.updateManagedService;
    if (result?.ok) {
      toast.success(next === 0 ? `Stopping ${name}` : `Scaling ${name} to ${next}`);
      onChanged();
    } else {
      toast.error(result?.errors?.[0]?.message ?? "Update failed");
    }
  }

  return { name, replicas: replicasOf(config), loading, setReplicas };
}
