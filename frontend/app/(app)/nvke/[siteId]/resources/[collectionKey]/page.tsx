import { notFound } from "next/navigation";
import { viewerHasPermission } from "@/lib/permissions/server-guard";
import { routeTitle } from "@/lib/i18n/route-title";
import { validNvkeGuid } from "@/lib/nvke/recovery";
import { validNvkeCollectionKey } from "@/lib/nvke/inventory";
import { NvkeInventoryClient } from "./inventory-client";
export const generateMetadata = routeTitle("nvke");
export default async function NvkeInventoryPage({
  params,
  searchParams,
}: {
  params: Promise<{ siteId: string; collectionKey: string }>;
  searchParams: Promise<{ snapshot?: string | string[] }>;
}) {
  const { siteId, collectionKey } = await params;
  const { snapshot } = await searchParams;
  if (
    !validNvkeGuid(siteId) ||
    !validNvkeCollectionKey(collectionKey) ||
    (snapshot !== undefined && !validNvkeGuid(snapshot)) ||
    !(await viewerHasPermission("nvke.read"))
  )
    notFound();
  return (
    <NvkeInventoryClient
      siteId={siteId}
      collectionKey={collectionKey}
      snapshotId={snapshot ?? null}
    />
  );
}
