"use client";

import { RolesView } from "@/components/screens/administration/permissions/RolesView";
import { useRoles } from "@/components/screens/administration/permissions/use-roles";

/** Mounted only while the Roles tab is shown, so its queries run only then. */
export function RolesTab() {
  const roles = useRoles();
  return <RolesView {...roles} />;
}
