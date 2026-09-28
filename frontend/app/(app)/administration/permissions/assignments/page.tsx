import { AssignmentsTab } from "../assignments-tab";
import { PermissionsClient } from "../permissions-client";

export const metadata = { title: "Assignments · Permissions · Astrolift" };

export default function PermissionsAssignmentsPage() {
  return (
    <PermissionsClient page="assignments">
      <AssignmentsTab />
    </PermissionsClient>
  );
}
