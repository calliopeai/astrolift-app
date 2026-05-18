import { GET_APP, LIST_WORKLOADS } from "@/graphql/registry/registry.queries";
import { PreloadQuery } from "@/lib/apollo";

import { TopologyClient } from "./topology-client";

export const metadata = {
  title: "Topology · Astrolift",
};

export default async function AppTopologyPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return (
    <PreloadQuery query={GET_APP} variables={{ slug }}>
      <PreloadQuery query={LIST_WORKLOADS} variables={{ appSlug: slug }}>
        <TopologyClient slug={slug} />
      </PreloadQuery>
    </PreloadQuery>
  );
}
