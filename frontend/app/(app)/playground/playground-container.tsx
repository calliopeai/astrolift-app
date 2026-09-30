"use client";
import { PlaygroundScreen } from "@/components/screens/playground/PlaygroundScreen";
import { usePlayground } from "@/components/screens/playground/use-playground";
import { PlaygroundBatchContainer } from "./playground-batch-container";
export function PlaygroundContainer() {
  const p = usePlayground();
  return (
    <PlaygroundScreen
      {...p}
      batch={
        <PlaygroundBatchContainer
          invoke={p.invoke}
          contextKey={p.contextKey}
          canSend={p.canSend}
          maxPromptChars={p.maxPromptChars}
        />
      }
    />
  );
}
