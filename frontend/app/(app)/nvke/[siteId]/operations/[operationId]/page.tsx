import { notFound } from "next/navigation";
import { viewerHasPermission } from "@/lib/permissions/server-guard";
import { routeTitle } from "@/lib/i18n/route-title";
import { validNvkeGuid } from "@/lib/nvke/recovery";
import { NvkeOperationClient } from "./operation-client";

export const generateMetadata = routeTitle("nvke");
export default async function NvkeOperationPage({
  params,
}: {
  params: Promise<{ siteId: string; operationId: string }>;
}) {
  const { siteId, operationId } = await params;
  if (
    !validNvkeGuid(siteId) ||
    !validNvkeGuid(operationId) ||
    !(await viewerHasPermission("nvke.read"))
  )
    notFound();
  return <NvkeOperationClient siteId={siteId} operationId={operationId} />;
}
