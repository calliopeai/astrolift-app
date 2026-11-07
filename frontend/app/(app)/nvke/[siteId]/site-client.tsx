"use client";
import { NvkeSiteScreen } from "@/components/screens/nvke/NvkeSiteScreen";
import { useNvkeSite } from "@/graphql/nvke/use-nvke-site";

export function NvkeSiteClient({ siteId }: { siteId: string }) {
  const { scopeKey, ...props } = useNvkeSite(siteId);
  return <NvkeSiteScreen key={scopeKey} {...props} />;
}
