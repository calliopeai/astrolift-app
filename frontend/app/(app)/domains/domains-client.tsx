"use client";

import { ManagedDomainsScreen } from "@/components/screens/domains/ManagedDomainsScreen";
import { useManagedDomains } from "@/components/screens/domains/use-managed-domains";

export function DomainsClient() {
  return <ManagedDomainsScreen {...useManagedDomains()} />;
}
