"use client";

import { RolesView } from "@/components/screens/administration/permissions/RolesView";
import { useRoles } from "@/components/screens/administration/permissions/use-roles";

/** Admin › Permissions › Roles, mounted only while the permissions console is on. */
export function RolesTab() {
  const roles = useRoles();
  return <RolesView {...roles} />;
}
