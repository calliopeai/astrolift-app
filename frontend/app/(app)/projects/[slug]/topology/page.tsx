import { routeTitle } from "@/lib/i18n/route-title";
import { ProjectTopologyClient } from "./project-topology-client";
export const generateMetadata = routeTitle("topology");
export default async function ProjectTopologyPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return <ProjectTopologyClient slug={slug} />;
}
