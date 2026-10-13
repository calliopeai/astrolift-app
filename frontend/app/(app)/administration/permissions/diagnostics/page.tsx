import { routeTitle } from "@/lib/i18n/route-title";

import { PermissionsClient } from "../permissions-client";
import { PermissionsDiagnosticsContent } from "../permissions-diagnostics-client";

export const generateMetadata = routeTitle("checkAccess", "permissions");

export default function PermissionsDiagnosticsPage() {
  return (
    <PermissionsClient page="diagnostics">
      <PermissionsDiagnosticsContent />
    </PermissionsClient>
  );
}
