import { routeTitle } from "@/lib/i18n/route-title";

import { PolicyClient } from "./policy-client";

export const generateMetadata = routeTitle("policy", "administration");

export default async function PolicyPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <PolicyClient id={id} />;
}
