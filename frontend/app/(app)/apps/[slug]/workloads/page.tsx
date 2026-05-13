import {
  GET_APP,
  LIST_WORKLOADS,
} from "@/graphql/registry/registry.queries";
import { PreloadQuery } from "@/lib/apollo";

import { WorkloadsListClient } from "./workloads-list-client";

export const metadata = {
  title: "Workloads · Astrolift",
};

export default async function AppWorkloadsPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return (
    <PreloadQuery query={GET_APP} variables={{ slug }}>
      <PreloadQuery query={LIST_WORKLOADS} variables={{ appSlug: slug }}>
        <WorkloadsListClient slug={slug} />
      </PreloadQuery>
    </PreloadQuery>
  );
}
