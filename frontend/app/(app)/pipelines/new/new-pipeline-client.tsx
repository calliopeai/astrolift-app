"use client";
import { NewPipelineScreen } from "@/components/screens/pipelines/NewPipelineScreen";
import { useNewPipeline } from "@/components/screens/pipelines/use-new-pipeline";
export function NewPipelineClient() {
  return <NewPipelineScreen {...useNewPipeline()} />;
}
