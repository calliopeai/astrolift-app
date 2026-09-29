import { PermissionsClient } from "../../permissions-client";
import { NewRoleClient } from "./new-role-client";

export const metadata = { title: "New role · Permissions · Astrolift" };

export default function NewRolePage() {
  return (
    <PermissionsClient page="roles">
      <NewRoleClient />
    </PermissionsClient>
  );
}
