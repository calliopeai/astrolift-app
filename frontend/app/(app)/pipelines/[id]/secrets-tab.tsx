"use client";

import { PipelineSecretsView } from "@/components/screens/pipelines/PipelineSecrets";
import { usePipelineSecrets } from "@/components/screens/pipelines/use-pipeline-secrets";

/** The pipeline Secrets tab (#100): hook plus view, mounted only while the tab is open. */
export function PipelineSecretsTab({ pipelineId }: { pipelineId: string }) {
  return <PipelineSecretsView {...usePipelineSecrets(pipelineId)} />;
}
