"use client";
import { PlaygroundHistoryScreen } from "@/components/screens/playground/PlaygroundHistoryScreen";
import { useSavedPlayground } from "@/components/screens/playground/use-saved-playground";
export function PlaygroundHistoryContainer() {
  return <PlaygroundHistoryScreen {...useSavedPlayground()} />;
}
