"use client";

import { DnsConnectionWizard } from "@/components/screens/domains/DnsConnectionWizard";
import { useDnsConnectionSetup } from "@/components/screens/domains/use-dns-connection-setup";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useMe } from "@/graphql/user/user.hooks";

function SetupContext({ domainId }: { domainId?: string }) {
  return <DnsConnectionWizard {...useDnsConnectionSetup(domainId)} />;
}

export function DnsConnectionClient({ domainId }: { domainId?: string }) {
  const { org } = useActiveOrg();
  const { user } = useMe();
  return (
    <SetupContext
      key={`${org?.id ?? ""}:${user?.id ?? ""}:${domainId ?? ""}`}
      domainId={domainId}
    />
  );
}
