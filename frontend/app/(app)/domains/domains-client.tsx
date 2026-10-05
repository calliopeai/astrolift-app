"use client";

import { ManagedDomainsScreen } from "@/components/screens/domains/ManagedDomainsScreen";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useMe } from "@/graphql/user/user.hooks";
import { useManagedDomains } from "@/components/screens/domains/use-managed-domains";
import { DnsConnectionCallbackNotice } from "@/components/screens/domains/DnsConnectionCallbackNotice";

function DomainContext() {
  return <ManagedDomainsScreen {...useManagedDomains()} />;
}

export function DomainsClient({
  connectionOutcome,
}: {
  connectionOutcome?: "saved" | "unconfirmed" | "denied";
}) {
  const { org } = useActiveOrg();
  const { user } = useMe();
  return (
    <>
      <DnsConnectionCallbackNotice outcome={connectionOutcome} />
      <DomainContext key={`${org?.id ?? ""}:${user?.id ?? ""}`} />
    </>
  );
}
