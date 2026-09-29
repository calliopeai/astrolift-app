"use client";

import { MinusIcon, PlusIcon } from "lucide-react";

import { Button } from "@/components/ui/button";

import type { useModelReplicas } from "./use-model-replicas";

const MAX_REPLICAS = 8;

export type ModelReplicasViewProps = ReturnType<typeof useModelReplicas>;

/** Stop, start and scale controls for one hosted vLLM model (#2040). */
export function ModelReplicasView({
  name,
  replicas,
  loading,
  setReplicas,
}: ModelReplicasViewProps) {
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
