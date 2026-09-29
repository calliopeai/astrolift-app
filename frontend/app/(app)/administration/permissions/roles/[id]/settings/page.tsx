import { PermissionsClient } from "../../../permissions-client";
import { RoleDetailClient } from "../role-detail-client";

export const metadata = { title: "Settings · Role · Permissions · Astrolift" };

export default async function RoleSettingsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <PermissionsClient page="roles">
      <RoleDetailClient id={id} tab="settings" />
    </PermissionsClient>
  );
}
