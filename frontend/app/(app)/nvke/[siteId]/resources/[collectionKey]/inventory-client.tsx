"use client";
import { NvkeInventoryScreen } from "@/components/screens/nvke/NvkeInventoryScreen";
import { useNvkeInventory } from "@/graphql/nvke/use-nvke-inventory";
export function NvkeInventoryClient({
  siteId,
  collectionKey,
  snapshotId,
}: {
  siteId: string;
  collectionKey: string;
  snapshotId: string | null;
}) {
  const { scopeKey, ...props } = useNvkeInventory(siteId, collectionKey, snapshotId);
  return <NvkeInventoryScreen key={scopeKey} {...props} />;
}
