import { PreloadQuery } from "@/lib/apollo";
import { LIST_API_TOKENS } from "@/graphql/identity/identity.queries";
import { routeTitle } from "@/lib/i18n/route-title";

import { TokenDetailClient } from "./token-detail-client";

export const generateMetadata = routeTitle("apiToken");

/**
 * API token detail (#1106) — drill-in target for a /tokens row.
 * Reuses LIST_API_TOKENS (no singular query exists).
 */
export default async function TokenDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <PreloadQuery query={LIST_API_TOKENS}>
      <TokenDetailClient id={id} />
    </PreloadQuery>
  );
}
