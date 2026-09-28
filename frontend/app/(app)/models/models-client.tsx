"use client";

import { ModelsScreen } from "@/components/screens/models/ModelsScreen";
import { useModels } from "@/components/screens/models/use-models";

import { DeployModelSheet } from "./deploy-model-sheet";
import { ModelReplicas } from "./model-replicas";
import { ModelTestDialog } from "./model-test-dialog";

export function ModelsClient() {
  const models = useModels();

  return (
    <ModelsScreen
      {...models}
      renderReplicas={(m) => (
        <ModelReplicas
          id={m.id}
          name={m.name}
          config={(m.config ?? {}) as Record<string, unknown>}
          onChanged={models.refetch}
        />
      )}
      renderTest={(m) => <ModelTestDialog id={m.id} name={m.name} />}
      renderDeploySheet={({ open, onOpenChange }) => (
        <DeployModelSheet open={open} onOpenChange={onOpenChange} onDeployed={models.refetch} />
      )}
    />
  );
}
