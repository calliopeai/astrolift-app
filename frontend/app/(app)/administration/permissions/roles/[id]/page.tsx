import { PermissionsClient } from "../../permissions-client";
import { RoleDetailClient } from "./role-detail-client";

export const metadata = { title: "Role · Permissions · Astrolift" };

export default async function RolePermissionsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <PermissionsClient page="roles">
      <RoleDetailClient id={id} tab="permissions" />
    </PermissionsClient>
  );
}
