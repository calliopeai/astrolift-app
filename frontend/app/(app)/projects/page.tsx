import {
  LIST_PROJECTS,
  LIST_TEAMS,
} from "@/graphql/identity/identity.queries";
import { PreloadQuery } from "@/lib/apollo";

import { ProjectsClient } from "./projects-client";

export const metadata = {
  title: "Projects · Astrolift",
};

export default function ProjectsPage() {
  return (
    <PreloadQuery query={LIST_PROJECTS}>
      <PreloadQuery query={LIST_TEAMS}>
        <ProjectsClient />
      </PreloadQuery>
    </PreloadQuery>
  );
}
