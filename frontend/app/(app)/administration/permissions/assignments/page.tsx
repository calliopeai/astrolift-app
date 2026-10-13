import { routeTitle } from "@/lib/i18n/route-title";

import { AssignmentsTab } from "../assignments-tab";
import { PermissionsClient } from "../permissions-client";

export const generateMetadata = routeTitle("assignments", "permissions");

export default function PermissionsAssignmentsPage() {
  return (
    <PermissionsClient page="assignments">
      <AssignmentsTab />
    </PermissionsClient>
  );
}
