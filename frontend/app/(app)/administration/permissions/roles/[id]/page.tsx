import { routeTitle } from "@/lib/i18n/route-title";

import { PermissionsClient } from "../../permissions-client";
import { RoleDetailClient } from "./role-detail-client";

export const generateMetadata = routeTitle("role", "permissions");

export default async function RolePermissionsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <PermissionsClient page="roles">
      <RoleDetailClient id={id} tab="permissions" />
    </PermissionsClient>
  );
}
