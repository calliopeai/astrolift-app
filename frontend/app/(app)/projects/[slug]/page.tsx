import { routeTitle } from "@/lib/i18n/route-title";

import { ProjectDetailClient } from "./project-detail-client";

export const generateMetadata = routeTitle("project");

export default async function ProjectDetailPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return <ProjectDetailClient slug={slug} />;
}
