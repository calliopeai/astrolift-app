"use client";

import { ManagedDomainDetail } from "@/components/screens/domains/ManagedDomainDetail";
import { useManagedDomain } from "@/components/screens/domains/use-managed-domain";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useMe } from "@/graphql/user/user.hooks";
import { useWithheldCapabilities } from "@/hooks/use-install-policy";

function DomainContext({ id }: { id: string }) {
  return (
    <ManagedDomainDetail
      id={id}
      {...useManagedDomain(id)}
      dnsRestriction={useWithheldCapabilities().dns}
    />
  );
}

export function DomainDetailClient({ id }: { id: string }) {
  const { org } = useActiveOrg();
  const { user } = useMe();
  return <DomainContext key={`${org?.id ?? ""}:${user?.id ?? ""}:${id}`} id={id} />;
}
