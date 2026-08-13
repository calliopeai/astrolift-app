import { ProjectDetailClient } from "./project-detail-client";

export const metadata = {
  title: "Project · Astrolift",
};

export default async function ProjectDetailPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return <ProjectDetailClient slug={slug} />;
}
