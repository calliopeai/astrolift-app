"use client";

import { TrustedDomainsCard as TrustedDomainsCardView } from "@/components/screens/administration/organization/TrustedDomainsCard";
import { useTrustedDomains } from "@/components/screens/administration/organization/use-trusted-domains";

/** The trusted email domains card wired to the org allowlist. */
export function TrustedDomainsCard() {
  return <TrustedDomainsCardView {...useTrustedDomains()} />;
}
