"use client";

import { ManagedDomainDetail } from "@/components/screens/domains/ManagedDomainDetail";
import { useManagedDomain } from "@/components/screens/domains/use-managed-domain";
import { useDomainDefaultSettings } from "@/components/screens/domains/use-domain-default-settings";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useMe } from "@/graphql/user/user.hooks";

function DomainContext({ id }: { id: string }) {
  const state = useManagedDomain(id);
  const defaults = useDomainDefaultSettings(state);
  return <ManagedDomainDetail id={id} {...state} defaultSettings={defaults} />;
}

export function DomainDetailClient({ id }: { id: string }) {
  const { org } = useActiveOrg();
  const { user } = useMe();
  return <DomainContext key={`${org?.id ?? ""}:${user?.id ?? ""}:${id}`} id={id} />;
}
