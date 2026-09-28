"use client";

import { PermissionsDiagnosticsView } from "@/components/screens/administration/permissions/PermissionsDiagnosticsView";
import { usePermissionsDiagnostics } from "@/components/screens/administration/permissions/use-permissions-diagnostics";

/** Mounted only while the Diagnostics tab is shown, so its queries run only then. */
export function PermissionsDiagnosticsContent() {
  const diagnostics = usePermissionsDiagnostics();
  return <PermissionsDiagnosticsView {...diagnostics} />;
}
