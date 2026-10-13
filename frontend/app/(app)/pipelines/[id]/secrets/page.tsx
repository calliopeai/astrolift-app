import { routeTitle } from "@/lib/i18n/route-title";

import { PipelineDetailClient } from "../pipeline-detail-client";

export const generateMetadata = routeTitle("pipelineSecrets");

export default async function PipelineSecretsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <PipelineDetailClient pipelineId={id} />;
}
