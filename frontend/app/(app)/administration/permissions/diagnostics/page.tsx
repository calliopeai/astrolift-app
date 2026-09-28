import { PermissionsClient } from "../permissions-client";
import { PermissionsDiagnosticsContent } from "../permissions-diagnostics-client";

export const metadata = { title: "Diagnostics · Permissions · Astrolift" };

export default function PermissionsDiagnosticsPage() {
  return (
    <PermissionsClient page="diagnostics">
      <PermissionsDiagnosticsContent />
    </PermissionsClient>
  );
}
