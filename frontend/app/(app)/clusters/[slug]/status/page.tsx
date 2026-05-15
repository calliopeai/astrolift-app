import { LIST_CLUSTERS } from "@/graphql/clusters/clusters.queries";
import { PreloadQuery } from "@/lib/apollo";

import { ClusterStatusClient } from "./cluster-status-client";

export const metadata = { title: "Cluster status · Astrolift" };

export default async function ClusterStatusPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return (
    <PreloadQuery query={LIST_CLUSTERS}>
      <ClusterStatusClient slug={slug} />
    </PreloadQuery>
  );
}
