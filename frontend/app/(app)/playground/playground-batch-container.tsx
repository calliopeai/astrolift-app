"use client";
import { PlaygroundBatch } from "@/components/screens/playground/PlaygroundBatch";
import { usePlaygroundBatch } from "@/components/screens/playground/use-playground-batch";
import type { PromptExecutor } from "@/components/screens/playground/playground.types";
/** Batch shares the page's real relay admission and per-user rate budget. */
export function PlaygroundBatchContainer(p: {
  invoke: PromptExecutor;
  contextKey: string;
  canSend: boolean;
  maxPromptChars: number;
}) {
  return (
    <PlaygroundBatch {...usePlaygroundBatch(p.invoke, p.contextKey, p.canSend, p.maxPromptChars)} />
  );
}
