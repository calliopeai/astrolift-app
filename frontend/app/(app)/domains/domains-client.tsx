"use client";

import { ManagedDomainsScreen } from "@/components/screens/domains/ManagedDomainsScreen";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useMe } from "@/graphql/user/user.hooks";
import { useWithheldCapabilities } from "@/hooks/use-install-policy";
import { useManagedDomains } from "@/components/screens/domains/use-managed-domains";
import { DnsConnectionCallbackNotice } from "@/components/screens/domains/DnsConnectionCallbackNotice";

function DomainContext() {
  return (
    <ManagedDomainsScreen {...useManagedDomains()} dnsRestriction={useWithheldCapabilities().dns} />
  );
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
