import { LIST_CLUSTERS } from "@/graphql/clusters/clusters.queries";
import { PreloadQuery } from "@/lib/apollo";

import { ClustersClient } from "./clusters-client";

export const metadata = { title: "Clusters · Astrolift" };

export default function ClustersPage() {
  return (
    <PreloadQuery query={LIST_CLUSTERS}>
      <ClustersClient />
    </PreloadQuery>
  );
}
