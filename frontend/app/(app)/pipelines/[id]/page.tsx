import { PageShell } from "@/components/PageShell";
import { PipelineDetailClient } from "./pipeline-detail-client";

export default function PipelineDetailPage({ params }: { params: { id: string } }) {
  return (
    <PageShell title="Pipeline" description="Pipeline run details, logs, and artifacts.">
      <PipelineDetailClient pipelineId={params.id} />
    </PageShell>
  );
}
