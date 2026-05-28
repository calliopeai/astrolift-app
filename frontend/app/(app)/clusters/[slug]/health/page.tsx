import { LIST_CLUSTERS } from "@/graphql/clusters/clusters.queries";
import { PreloadQuery } from "@/lib/apollo";

import { ClusterHealthClient } from "./cluster-health-client";

export const metadata = { title: "Cluster health · Astrolift" };

export default async function ClusterHealthPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return (
    <PreloadQuery query={LIST_CLUSTERS}>
      <ClusterHealthClient slug={slug} />
    </PreloadQuery>
  );
}
