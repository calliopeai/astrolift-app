"use client";

import { ProjectResourcesScreen } from "@/components/screens/projects/ProjectResourcesScreen";
import { useProjectResources } from "@/components/screens/projects/use-project-resources";

export function ProjectResourcesClient({ slug }: { slug: string }) {
  return <ProjectResourcesScreen {...useProjectResources(slug)} />;
}
