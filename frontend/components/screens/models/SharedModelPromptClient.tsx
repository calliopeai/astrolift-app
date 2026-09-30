"use client";
import type { ClusterModelFieldsFragment } from "@/graphql/__generated__/operations";
import { SharedModelPromptPanel } from "./SharedModelPromptPanel";
import { useSharedModelPrompt } from "./use-shared-model-prompt";
export function SharedModelPromptClient({ model }: { model: ClusterModelFieldsFragment }) {
  return (
    <PromptContext
      key={JSON.stringify([
        model.organizationId,
        model.id,
        model.version,
        model.clusterId,
        model.providerId,
      ])}
      model={model}
    />
  );
}
function PromptContext({ model }: { model: ClusterModelFieldsFragment }) {
  return <SharedModelPromptPanel {...useSharedModelPrompt(model)} />;
}
