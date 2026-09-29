"use client";

import { ModelTestDialogView } from "@/components/screens/models/ModelTestDialog";
import { useModelTest } from "@/components/screens/models/use-model-test";

/** Test dialog for one hosted vLLM model; the mutation runs per row. */
export function ModelTestDialog({ id, name }: { id: string; name: string }) {
  return <ModelTestDialogView {...useModelTest({ id, name })} />;
}
