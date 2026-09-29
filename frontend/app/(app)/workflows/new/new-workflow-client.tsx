"use client";

import { ConfigureWorkflowScreen } from "@/components/screens/workflows/new/ConfigureWorkflowScreen";
import { useConfigureWorkflow } from "@/components/screens/workflows/new/use-configure-workflow";
import { useWorkflowBuilder } from "@/components/screens/workflows/new/use-workflow-builder";
import { WorkflowBuilderScreen } from "@/components/screens/workflows/new/WorkflowBuilderScreen";
import { parsePattern } from "@/components/screens/workflows/new/workflow-patterns";

/** A new workflow from a pattern or manifest, or one configured from a definition. */
export function NewWorkflowClient({
  definitionSlug,
  pattern,
}: {
  definitionSlug: string | null;
  pattern: string | null;
}) {
  return definitionSlug ? (
    <ConfigureFromDefinition definitionSlug={definitionSlug} />
  ) : (
    <NewDefinition pattern={pattern} />
  );
}

function ConfigureFromDefinition({ definitionSlug }: { definitionSlug: string }) {
  return <ConfigureWorkflowScreen {...useConfigureWorkflow(definitionSlug)} />;
}

function NewDefinition({ pattern }: { pattern: string | null }) {
  return <WorkflowBuilderScreen {...useWorkflowBuilder()} initialPattern={parsePattern(pattern)} />;
}
