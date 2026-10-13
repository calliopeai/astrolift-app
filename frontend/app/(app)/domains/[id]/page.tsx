import { routeTitle } from "@/lib/i18n/route-title";

import { DomainDetailClient } from "./domain-detail-client";

export const generateMetadata = routeTitle("managedDomain");

/** Exact domain reads run in the actor/org-bound client, without preloading the capped inventory. */
export default async function DomainDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <DomainDetailClient id={id} />;
}
