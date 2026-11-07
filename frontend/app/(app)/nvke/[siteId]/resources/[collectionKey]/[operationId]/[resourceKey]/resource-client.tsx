"use client";
import { NvkeResourceScreen } from "@/components/screens/nvke/NvkeResourceScreen";
import { useNvkeResource } from "@/graphql/nvke/use-nvke-inventory";
export function NvkeResourceClient({
  siteId,
  collectionKey,
  operationId,
  resourceKey,
}: {
  siteId: string;
  collectionKey: string;
  operationId: string;
  resourceKey: string;
}) {
  const { scopeKey, ...props } = useNvkeResource(siteId, collectionKey, operationId, resourceKey);
  return <NvkeResourceScreen key={scopeKey} {...props} />;
}
