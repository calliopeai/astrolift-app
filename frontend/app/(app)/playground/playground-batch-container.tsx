"use client";

import { PlaygroundBatch } from "@/components/screens/playground/PlaygroundBatch";
import { usePlaygroundBatch } from "@/components/screens/playground/use-playground-batch";

/** Mounted only while the batch tab is shown, so its state resets with the tab as before. */
export function PlaygroundBatchContainer({ model }: { model: string }) {
  return <PlaygroundBatch {...usePlaygroundBatch(model)} />;
}
