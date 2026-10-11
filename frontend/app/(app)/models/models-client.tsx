"use client";
import { SharedModelsScreen } from "@/components/screens/models/SharedModelsScreen";
import { useSharedModels } from "@/components/screens/models/use-shared-models";
import { useInstallManagedModel } from "@/hooks/use-install-policy";
export function ModelsClient() {
  return <SharedModelsScreen {...useSharedModels()} installModel={useInstallManagedModel()} />;
}
