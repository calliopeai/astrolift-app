"use client";

import { ProjectDetailScreen } from "@/components/screens/projects/ProjectDetailScreen";
import { useProjectDetail } from "@/components/screens/projects/use-project-detail";

export function ProjectDetailClient({ slug }: { slug: string }) {
  return <ProjectDetailScreen {...useProjectDetail(slug)} />;
}
