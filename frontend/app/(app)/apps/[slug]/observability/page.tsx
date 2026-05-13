import { LIST_DEPLOYMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import { LIST_EVENTS } from "@/graphql/operations/operations.queries";
import { GET_APP } from "@/graphql/registry/registry.queries";
import { PreloadQuery } from "@/lib/apollo";

import { ObservabilityClient } from "./observability-client";

export const metadata = {
  title: "Observability · Astrolift",
};

export default async function AppObservabilityPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return (
    <PreloadQuery query={GET_APP} variables={{ slug }}>
      <PreloadQuery
        query={LIST_DEPLOYMENTS}
        variables={{ appSlug: slug, limit: 50 }}
      >
        <PreloadQuery query={LIST_EVENTS} variables={{ limit: 200 }}>
          <ObservabilityClient slug={slug} />
        </PreloadQuery>
      </PreloadQuery>
    </PreloadQuery>
  );
}
