import { notFound } from "next/navigation";
import { viewerHasPermission } from "@/lib/permissions/server-guard";
import { routeTitle } from "@/lib/i18n/route-title";
import { validNvkeGuid } from "@/lib/nvke/recovery";
import { NvkeResourcesClient } from "./resources-client";
export const generateMetadata = routeTitle("nvke");
export default async function NvkeResourcesPage({
  params,
}: {
  params: Promise<{ siteId: string }>;
}) {
  const { siteId } = await params;
  if (!validNvkeGuid(siteId) || !(await viewerHasPermission("nvke.read"))) notFound();
  return <NvkeResourcesClient siteId={siteId} />;
}
