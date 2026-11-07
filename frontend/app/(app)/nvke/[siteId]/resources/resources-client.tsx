"use client";
import { NvkeResourcesScreen } from "@/components/screens/nvke/NvkeResourcesScreen";
import { useNvkeResources } from "@/graphql/nvke/use-nvke-inventory";
export function NvkeResourcesClient({ siteId }: { siteId: string }) {
  const { scopeKey, ...props } = useNvkeResources(siteId);
  return <NvkeResourcesScreen key={scopeKey} {...props} />;
}
