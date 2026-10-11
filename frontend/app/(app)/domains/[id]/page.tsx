import { DomainDetailClient } from "./domain-detail-client";

export const metadata = { title: "Managed domain · Astrolift" };

/** Exact domain reads run in the actor/org-bound client, without preloading the capped inventory. */
export default async function DomainDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <DomainDetailClient id={id} />;
}
