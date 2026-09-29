"use client";

import { useSearchParams } from "next/navigation";

import { WorkflowBuilderScreen } from "@/components/screens/workflows/new/WorkflowBuilderScreen";
import { useWorkflowBuilder } from "@/components/screens/workflows/new/use-workflow-builder";
import { parsePattern } from "@/components/screens/workflows/new/workflow-patterns";

export default function NewWorkflowBuilderPage() {
  const searchParams = useSearchParams();
  return (
    <WorkflowBuilderScreen
      {...useWorkflowBuilder()}
      initialPattern={parsePattern(searchParams.get("pattern"))}
    />
  );
}
