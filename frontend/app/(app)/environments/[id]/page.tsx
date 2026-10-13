import { PreloadQuery } from "@/lib/apollo";
import { LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import { routeTitle } from "@/lib/i18n/route-title";

import { EnvironmentDetailClient } from "./environment-detail-client";

export const generateMetadata = routeTitle("environment");

/**
 * Environment detail (#1106) — drill-in target for a global /environments row.
 * Reuses the global LIST_ENVIRONMENTS window (no singular query exists).
 */
export default async function EnvironmentDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <PreloadQuery query={LIST_ENVIRONMENTS} variables={{ appSlug: null }}>
      <EnvironmentDetailClient id={id} />
    </PreloadQuery>
  );
}
