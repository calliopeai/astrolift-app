"use client";

import { useVisualizations } from "@/components/screens/settings/visualizations/use-visualizations";
import { VisualizationsScreen } from "@/components/screens/settings/visualizations/VisualizationsScreen";

export function VisualizationsClient() {
  return <VisualizationsScreen {...useVisualizations()} />;
}
