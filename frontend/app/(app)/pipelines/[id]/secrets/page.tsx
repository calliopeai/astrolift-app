import { PipelineDetailClient } from "../pipeline-detail-client";

export const metadata = { title: "Pipeline Secrets · Astrolift" };

export default function PipelineSecretsPage({
  params,
}: {
  params: { id: string };
}) {
  return <PipelineDetailClient pipelineId={params.id} />;
}
