import {
  GET_APP,
  LIST_WORKLOADS,
} from "@/graphql/registry/registry.queries";
import { PreloadQuery } from "@/lib/apollo";

import { AppDetailClient } from "./app-detail-client";

export const metadata = { title: "App · Astrolift" };

export default async function AppDetailPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return (
    <PreloadQuery query={GET_APP} variables={{ slug }}>
      <PreloadQuery query={LIST_WORKLOADS} variables={{ appSlug: slug }}>
        <AppDetailClient slug={slug} />
      </PreloadQuery>
    </PreloadQuery>
  );
}
