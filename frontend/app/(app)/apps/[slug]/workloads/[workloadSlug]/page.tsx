import {
  GET_WORKLOAD_POD_STATUS_BREAKDOWN,
  LIST_APP_PODS,
} from "@/graphql/lifecycle/lifecycle.queries";
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
      <PreloadQuery query={LIST_CONTAINERS} variables={{ workloadSlug }}>
        <PreloadQuery query={LIST_APP_PODS} variables={{ appSlug: slug }}>
          <PreloadQuery
            query={GET_WORKLOAD_POD_STATUS_BREAKDOWN}
            variables={{ appSlug: slug, workloadSlug }}
          >
            <WorkloadDetailClient
              appSlug={slug}
              workloadSlug={workloadSlug}
            />
          </PreloadQuery>
        </PreloadQuery>
      </PreloadQuery>
    </PreloadQuery>
  );
}
