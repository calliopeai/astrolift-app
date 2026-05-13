import { LIST_ENVIRONMENTS, LIST_DEPLOYMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import { GET_APP } from "@/graphql/registry/registry.queries";
import { PreloadQuery } from "@/lib/apollo";

import { AppDeploymentsClient } from "./deployments-client";

export const metadata = {
  title: "Deployments · Astrolift",
};

export default async function AppDeploymentsPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return (
    <PreloadQuery query={GET_APP} variables={{ slug }}>
      <PreloadQuery
        query={LIST_DEPLOYMENTS}
        variables={{ appSlug: slug, limit: 100 }}
      >
        <PreloadQuery query={LIST_ENVIRONMENTS} variables={{ appSlug: slug }}>
          <AppDeploymentsClient slug={slug} />
        </PreloadQuery>
      </PreloadQuery>
    </PreloadQuery>
  );
}
