import { LIST_PREVIEW_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import { GET_APP } from "@/graphql/registry/registry.queries";
import { PreloadQuery } from "@/lib/apollo";

import { AppPreviewsClient } from "./previews-client";

export const metadata = {
  title: "Previews · Astrolift",
};

export default async function AppPreviewsPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return (
    <PreloadQuery query={GET_APP} variables={{ slug }}>
      <PreloadQuery
        query={LIST_PREVIEW_ENVIRONMENTS}
        variables={{ appSlug: slug }}
      >
        <AppPreviewsClient slug={slug} />
      </PreloadQuery>
    </PreloadQuery>
  );
}
