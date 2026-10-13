import { routeTitle } from "@/lib/i18n/route-title";

import { DomainEmailClient } from "./domain-email-client";
export const generateMetadata = routeTitle("emailDelivery");
export default async function DomainEmailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <DomainEmailClient id={id} />;
}
