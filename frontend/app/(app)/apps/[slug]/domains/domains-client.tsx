"use client";

import { DomainsScreen } from "@/components/screens/apps/domains/DomainsScreen";
import { useAppDomains } from "@/components/screens/apps/domains/use-app-domains";
import { useWithheldCapabilities } from "@/hooks/use-install-policy";

import { AppTabs } from "../components/app-tabs";

export function AppDomainsClient({ slug }: { slug: string }) {
  const domains = useAppDomains(slug);
  // The installer's Deny is on AWS, so only an app on an AWS cluster is affected.
  const dns = useWithheldCapabilities({ skip: domains.clusterProviderSlug !== "aws" }).dns;
  return (
    <DomainsScreen
      {...domains}
      slug={slug}
      tabs={<AppTabs slug={slug} active="domains" />}
      dnsRestriction={dns}
    />
  );
}
