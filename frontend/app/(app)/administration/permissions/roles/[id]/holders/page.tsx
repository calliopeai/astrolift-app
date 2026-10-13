import { routeTitle } from "@/lib/i18n/route-title";

import { PermissionsClient } from "../../../permissions-client";
import { RoleDetailClient } from "../role-detail-client";

export const generateMetadata = routeTitle("holders", "role", "permissions");

export default async function RoleHoldersPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <PermissionsClient page="roles">
      <RoleDetailClient id={id} tab="holders" />
    </PermissionsClient>
  );
}
