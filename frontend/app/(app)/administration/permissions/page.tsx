import { PermissionsClient } from "./permissions-client";
import { RolesTab } from "./roles-tab";

export const metadata = { title: "Roles · Permissions · Astrolift" };

export default function PermissionsPage() {
  return (
    <PermissionsClient page="roles">
      <RolesTab />
    </PermissionsClient>
  );
}
