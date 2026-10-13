import { routeTitle } from "@/lib/i18n/route-title";

import { ProjectResourcesClient } from "./project-resources-client";

export const generateMetadata = routeTitle("projectResources");

export default async function ProjectResourcesPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return <ProjectResourcesClient slug={slug} />;
}
