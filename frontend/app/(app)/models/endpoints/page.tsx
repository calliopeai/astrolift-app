"use client";
import { ModelsScreen } from "@/components/screens/models/ModelsScreen";
import { useModels } from "@/components/screens/models/use-models";
export default function ModelEndpointsPage() {
  return <ModelsScreen {...useModels()} />;
}
