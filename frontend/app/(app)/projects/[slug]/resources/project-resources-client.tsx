"use client";

import { ProjectResourcesScreen } from "@/components/screens/projects/ProjectResourcesScreen";
import { useProjectResources } from "@/components/screens/projects/use-project-resources";
import { useSettingsSection } from "@/components/settings/use-settings-section";

/** Managed infrastructure or shared secret bundles, one at a time (`?section=`). */
export function ProjectResourcesClient({ slug }: { slug: string }) {
  return <ProjectResourcesScreen {...useProjectResources(slug)} section={useSettingsSection()} />;
}
