import { GET_RENDERED_MANIFEST } from "@/graphql/registry/registry.queries";
import { LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import { PreloadQuery } from "@/lib/apollo";

import { ManifestPreviewClient } from "./manifest-preview-client";

export const metadata = { title: "Manifest preview · Astrolift" };

export default async function ManifestPreviewPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return (
    <PreloadQuery
      query={GET_RENDERED_MANIFEST}
      variables={{ appSlug: slug, environmentName: null, imageTag: null }}
    >
      <PreloadQuery query={LIST_ENVIRONMENTS} variables={{ appSlug: slug }}>
        <ManifestPreviewClient slug={slug} />
      </PreloadQuery>
    </PreloadQuery>
  );
}
