import { PipelineDetailClient } from "../pipeline-detail-client";

export const metadata = { title: "Pipeline Secrets · Astrolift" };

export default async function PipelineSecretsPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <PipelineDetailClient pipelineId={id} />;
}
