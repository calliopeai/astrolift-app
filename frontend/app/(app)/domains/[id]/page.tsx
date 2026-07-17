import { PreloadQuery } from "@/lib/apollo";
import { LIST_MANAGED_DOMAINS } from "@/graphql/clusters/clusters.queries";

import { DomainDetailClient } from "./domain-detail-client";

export const metadata = { title: "Managed domain · Astrolift" };

/**
 * Managed domain detail (#1106) — drill-in target for a /domains row.
 * Reuses LIST_MANAGED_DOMAINS (no singular query exists).
 */
export default async function DomainDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <PreloadQuery query={LIST_MANAGED_DOMAINS}>
      <DomainDetailClient id={id} />
    </PreloadQuery>
  );
}
