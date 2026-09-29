"use client";

import { ManagedDomainDetail } from "@/components/screens/domains/ManagedDomainDetail";
import { useManagedDomain } from "@/components/screens/domains/use-managed-domain";

/** Managed domain detail (#1106), the drill-in target for a /domains row. */
export function DomainDetailClient({ id }: { id: string }) {
  return <ManagedDomainDetail id={id} {...useManagedDomain(id)} />;
}
