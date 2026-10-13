import { routeTitle } from "@/lib/i18n/route-title";

import { PermissionsClient } from "../../permissions-client";
import { NewRoleClient } from "./new-role-client";

export const generateMetadata = routeTitle("newRole", "permissions");

export default function NewRolePage() {
  return (
    <PermissionsClient page="roles">
      <NewRoleClient />
    </PermissionsClient>
  );
}
