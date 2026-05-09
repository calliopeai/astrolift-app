import {
  GET_WORKLOAD,
  LIST_CONTAINERS,
} from "@/graphql/registry/registry.queries";
import { PreloadQuery } from "@/lib/apollo";

import { WorkloadDetailClient } from "./workload-detail-client";

export const metadata = { title: "Workload · Astrolift" };

export default async function WorkloadDetailPage({
  params,
}: {
  params: Promise<{ slug: string; workloadSlug: string }>;
}) {
  const { slug, workloadSlug } = await params;
  return (
    <PreloadQuery
      query={GET_WORKLOAD}
      variables={{ appSlug: slug, slug: workloadSlug }}
    >
      <PreloadQuery
        query={LIST_CONTAINERS}
        variables={{ workloadSlug }}
      >
        <WorkloadDetailClient appSlug={slug} workloadSlug={workloadSlug} />
      </PreloadQuery>
    </PreloadQuery>
  );
}
