"use client";

import { ModelReplicasView } from "@/components/screens/models/ModelReplicas";
import { useModelReplicas } from "@/components/screens/models/use-model-replicas";

/** Replicas controls for one hosted vLLM model; the mutation runs per row. */
export function ModelReplicas(props: Parameters<typeof useModelReplicas>[0]) {
  return <ModelReplicasView {...useModelReplicas(props)} />;
}
