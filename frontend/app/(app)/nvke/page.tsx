import { notFound } from "next/navigation";
import { viewerHasPermission } from "@/lib/permissions/server-guard";
import { routeTitle } from "@/lib/i18n/route-title";
import { NvkeSitesClient } from "./sites-client";

export const generateMetadata = routeTitle("nvke");
export default async function NvkeSitesPage() {
  if (!(await viewerHasPermission("nvke.read"))) notFound();
  return <NvkeSitesClient />;
}
