import { notFound } from "next/navigation";
import { viewerHasPermission } from "@/lib/permissions/server-guard";
import { routeTitle } from "@/lib/i18n/route-title";
import { validNvkeGuid } from "@/lib/nvke/recovery";
import { validNvkeCollectionKey } from "@/lib/nvke/inventory";
import { NvkeResourceClient } from "./resource-client";
export const generateMetadata = routeTitle("nvke");
export default async function NvkeResourcePage({
  params,
}: {
  params: Promise<{
    siteId: string;
    collectionKey: string;
    operationId: string;
    resourceKey: string;
  }>;
}) {
  const values = await params;
  if (
    !validNvkeGuid(values.siteId) ||
    !validNvkeGuid(values.operationId) ||
    !validNvkeCollectionKey(values.collectionKey) ||
    values.resourceKey.length > 4096 ||
    !/^[A-Za-z0-9_:-]+$/.test(values.resourceKey) ||
    !(await viewerHasPermission("nvke.read"))
  )
    notFound();
  return <NvkeResourceClient {...values} />;
}
