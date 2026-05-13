import {
  LIST_MEMBERS,
  LIST_PROJECTS,
} from "@/graphql/identity/identity.queries";
import { LIST_APPS } from "@/graphql/registry/registry.queries";
import { PreloadQuery } from "@/lib/apollo";

import { ProjectDetailClient } from "./project-detail-client";

export const metadata = {
  title: "Project · Astrolift",
};

export default async function ProjectDetailPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return (
    <PreloadQuery query={LIST_PROJECTS}>
      <PreloadQuery query={LIST_APPS}>
        <PreloadQuery query={LIST_MEMBERS}>
          <ProjectDetailClient slug={slug} />
        </PreloadQuery>
      </PreloadQuery>
    </PreloadQuery>
  );
}
