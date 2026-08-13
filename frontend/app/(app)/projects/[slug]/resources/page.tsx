import { ProjectResourcesClient } from "./project-resources-client";

export const metadata = { title: "Project resources · Astrolift" };

export default async function ProjectResourcesPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return <ProjectResourcesClient slug={slug} />;
}
