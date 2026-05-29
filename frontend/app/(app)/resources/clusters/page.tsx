import {
  LIST_CLUSTERS,
  LIST_PROVIDER_PLUGINS,
} from "@/graphql/clusters/clusters.queries";
import { PreloadQuery } from "@/lib/apollo";

import { ConnectedClustersClient } from "./clusters-client";

export const metadata = {
  title: "Connected clusters · Resources · Astrolift",
};

export default function ConnectedClustersPage() {
  return (
    <PreloadQuery query={LIST_CLUSTERS}>
      <PreloadQuery query={LIST_PROVIDER_PLUGINS}>
        <ConnectedClustersClient />
      </PreloadQuery>
    </PreloadQuery>
  );
}
