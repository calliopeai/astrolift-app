import { PreloadQuery } from "@/lib/apollo";
import { LIST_PREVIEW_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";

import { PreviewDetailClient } from "./preview-detail-client";

export const metadata = { title: "Preview · Astrolift" };

/**
 * Preview environment detail (#1106) — drill-in target for a /previews row.
 * Reuses the global LIST_PREVIEW_ENVIRONMENTS window (no singular query exists).
 */
export default async function PreviewDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <PreloadQuery query={LIST_PREVIEW_ENVIRONMENTS} variables={{ appSlug: null }}>
      <PreviewDetailClient id={id} />
    </PreloadQuery>
  );
}
