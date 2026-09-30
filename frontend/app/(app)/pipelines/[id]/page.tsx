import { PipelineDetailClient } from "./pipeline-detail-client";

export const metadata = { title: "Pipeline · Astrolift" };

export default async function PipelineDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <PipelineDetailClient pipelineId={id} />;
}
