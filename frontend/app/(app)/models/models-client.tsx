"use client";
import { SharedModelsScreen } from "@/components/screens/models/SharedModelsScreen";
import { useSharedModels } from "@/components/screens/models/use-shared-models";
export function ModelsClient() {
  return <SharedModelsScreen {...useSharedModels()} />;
}
