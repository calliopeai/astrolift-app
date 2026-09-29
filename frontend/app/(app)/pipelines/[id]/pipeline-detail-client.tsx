"use client";

import { PipelineDetailScreen, RunGraphView } from "@/components/screens/pipelines/PipelineDetail";
import { usePipelineDetail, useRunGraph } from "@/components/screens/pipelines/use-pipeline-detail";

import { PipelineSecretsTab } from "./secrets-tab";

/**
 * A pipeline's detail tabs. The screen owns the markup; the latest run's
 * graph and the Secrets tab each sit in a container so their hooks run only
 * when shown.
 */
export function PipelineDetailClient({ pipelineId }: { pipelineId: string }) {
  const detail = usePipelineDetail(pipelineId);
  const latest = detail.runs[0];
  return (
    <PipelineDetailScreen
      {...detail}
      // key on the run id so the graph re-flows when the newest run changes
      runGraph={latest ? <RunGraph key={latest.id} runId={latest.id} /> : null}
      secrets={<PipelineSecretsTab pipelineId={pipelineId} />}
    />
  );
}

function RunGraph({ runId }: { runId: string }) {
  return <RunGraphView {...useRunGraph(runId)} />;
}
