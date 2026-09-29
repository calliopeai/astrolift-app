"use client";

import { PermissionsDiagnosticsView } from "@/components/screens/administration/permissions/PermissionsDiagnosticsView";
import { usePermissionsDiagnostics } from "@/components/screens/administration/permissions/use-permissions-diagnostics";

/** Admin › Permissions › Diagnostics, mounted only while the permissions console is on. */
export function PermissionsDiagnosticsContent() {
  const diagnostics = usePermissionsDiagnostics();
  return <PermissionsDiagnosticsView {...diagnostics} />;
}
