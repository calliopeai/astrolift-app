"use client";
import { NvkeSitesScreen } from "@/components/screens/nvke/NvkeSitesScreen";
import { useNvkeSites } from "@/graphql/nvke/nvke.hooks";

export function NvkeSitesClient() {
  const { scopeKey, ...props } = useNvkeSites();
  return <NvkeSitesScreen key={scopeKey} {...props} />;
}
