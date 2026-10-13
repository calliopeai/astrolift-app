import { routeTitle } from "@/lib/i18n/route-title";

import { PipelineDetailClient } from "./pipeline-detail-client";

export const generateMetadata = routeTitle("pipeline");

export default async function PipelineDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <PipelineDetailClient pipelineId={id} />;
}
