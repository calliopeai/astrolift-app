import { routeTitle } from "@/lib/i18n/route-title";

import { PermissionsClient } from "./permissions-client";
import { RolesTab } from "./roles-tab";

export const generateMetadata = routeTitle("roles", "permissions");

export default function PermissionsPage() {
  return (
    <PermissionsClient page="roles">
      <RolesTab />
    </PermissionsClient>
  );
}
