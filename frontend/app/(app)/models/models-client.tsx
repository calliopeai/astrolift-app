"use client";

import { ModelsScreen } from "@/components/screens/models/ModelsScreen";
import { useModels } from "@/components/screens/models/use-models";

export function ModelsClient() {
  return <ModelsScreen {...useModels()} />;
}
