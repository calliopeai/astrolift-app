"use client";

import { PermissionsScreen } from "@/components/screens/administration/permissions/PermissionsScreen";
import { FEATURE_FLAG_ADMIN_PERMISSIONS, useFeatureFlag } from "@/graphql/server/server.hooks";

import { AssignmentsTab } from "./assignments-tab";
import { PermissionsDiagnosticsContent } from "./permissions-diagnostics-client";
import { RolesTab } from "./roles-tab";

export function PermissionsClient() {
  const enabled = useFeatureFlag(FEATURE_FLAG_ADMIN_PERMISSIONS);

  return (
    <PermissionsScreen
      enabled={enabled}
      roles={<RolesTab />}
      assignments={<AssignmentsTab />}
      diagnostics={<PermissionsDiagnosticsContent />}
    />
  );
}
