import { LIST_PROJECTS, LIST_TEAMS } from "@/graphql/identity/identity.queries";
import { PreloadQuery } from "@/lib/apollo";

import { ProjectsClient } from "@/app/(app)/projects/projects-client";
import { routeTitle } from "@/lib/i18n/route-title";

export const generateMetadata = routeTitle("projects");

export default function AdministrationProjectsPage() {
  return (
    <PreloadQuery query={LIST_PROJECTS}>
      <PreloadQuery query={LIST_TEAMS}>
        <ProjectsClient />
      </PreloadQuery>
    </PreloadQuery>
  );
}
