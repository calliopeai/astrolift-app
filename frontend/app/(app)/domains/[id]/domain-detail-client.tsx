"use client";

import { ManagedDomainDetail } from "@/components/screens/domains/ManagedDomainDetail";
import { useManagedDomain } from "@/components/screens/domains/use-managed-domain";
import { useDomainDefaultSettings } from "@/components/screens/domains/use-domain-default-settings";
import { useDnsDelegation } from "@/components/screens/domains/use-dns-delegation";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useMe } from "@/graphql/user/user.hooks";
import { useWithheldCapabilities } from "@/hooks/use-install-policy";

function DomainContext({ id }: { id: string }) {
  const state = useManagedDomain(id);
  const defaults = useDomainDefaultSettings(state);
  const delegation = useDnsDelegation(state);
  return (
    <ManagedDomainDetail
      id={id}
      {...state}
      defaultSettings={defaults}
      delegation={delegation}
      dnsRestriction={useWithheldCapabilities().dns}
    />
  );
}

export function DomainDetailClient({ id }: { id: string }) {
  const { org } = useActiveOrg();
  const { user } = useMe();
  return <DomainContext key={`${org?.id ?? ""}:${user?.id ?? ""}:${id}`} id={id} />;
}
