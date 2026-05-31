import { PipelineDetailClient } from "./pipeline-detail-client";

export const metadata = { title: "Pipeline · Astrolift" };

export default function PipelinePage({ params }: { params: { id: string } }) {
  return <PipelineDetailClient id={params.id} />;
}
