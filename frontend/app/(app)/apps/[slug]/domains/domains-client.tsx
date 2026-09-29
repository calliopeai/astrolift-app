"use client";

import { DomainsScreen } from "@/components/screens/apps/domains/DomainsScreen";
import { useAppDomains } from "@/components/screens/apps/domains/use-app-domains";

import { AppTabs } from "../components/app-tabs";

export function AppDomainsClient({ slug }: { slug: string }) {
  const domains = useAppDomains(slug);
  return <DomainsScreen {...domains} slug={slug} tabs={<AppTabs slug={slug} active="domains" />} />;
}
