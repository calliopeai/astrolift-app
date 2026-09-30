"use client";
import { PlaygroundStarredScreen } from "@/components/screens/playground/PlaygroundStarredScreen";
import { useSavedPlayground } from "@/components/screens/playground/use-saved-playground";
export function PlaygroundStarredContainer() {
  const saved = useSavedPlayground(true);
  return (
    <PlaygroundStarredScreen
      items={saved.sessions}
      loading={saved.loading}
      error={saved.error}
      onRetry={saved.onRetry}
    />
  );
}
