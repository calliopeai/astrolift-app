"use client";

import { useMutation } from "@apollo/client/react";
import { MinusIcon, PlusIcon } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { UPDATE_MANAGED_SERVICE } from "@/graphql/services/services.mutations";

// UPDATE_MANAGED_SERVICE interpolates its field list, so codegen leaves it
// untyped; these are the only fields read here.
interface UpdateResult {
  updateManagedService: { ok: boolean; errors?: { message: string }[] | null };
}

const MAX_REPLICAS = 8;

export function replicasOf(config: Record<string, unknown>): number {
  const value = Number(config.replicas ?? 1);
  return Number.isFinite(value) ? value : 1;
}

/**
 * Stop, start and scale a hosted vLLM model (#2040). ``replicas`` is one of
 * the driver's in-place fields, so this is an update, never a reprovision;
 * the whole config goes back with only ``replicas`` changed.
 */
export function ModelReplicas({
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
  const replicas = replicasOf(config);

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

  return (
    <div className="flex items-center gap-1">
      <Button
        size="icon"
        variant="ghost"
        aria-label={`Scale ${name} down`}
        disabled={loading || replicas <= 1}
        onClick={() => setReplicas(replicas - 1)}
      >
        <MinusIcon className="size-3.5" />
      </Button>
      <span className="w-6 text-center text-sm tabular-nums" aria-label={`${name} replicas`}>
        {replicas}
      </span>
      <Button
        size="icon"
        variant="ghost"
        aria-label={`Scale ${name} up`}
        disabled={loading || replicas >= MAX_REPLICAS}
        onClick={() => setReplicas(replicas + 1)}
      >
        <PlusIcon className="size-3.5" />
      </Button>
      <Button
        size="sm"
        variant="outline"
        disabled={loading}
        onClick={() => setReplicas(replicas === 0 ? 1 : 0)}
      >
        {replicas === 0 ? "Start" : "Stop"}
      </Button>
    </div>
  );
}
