"use client";

import { AssignmentsView } from "@/components/screens/administration/permissions/AssignmentsView";
import { useAssignments } from "@/components/screens/administration/permissions/use-assignments";

import { GrantRoleDialog } from "@/components/screens/members/GrantRoleDialog";

/** Admin › Permissions › Assignments, mounted only while the permissions console is on. */
export function AssignmentsTab() {
  const { roles, rolesKnown, rolesError, onRetryRoles, ...assignments } = useAssignments();
  return (
    <AssignmentsView
      {...assignments}
      renderGrantDialog={({ open, onOpenChange }) => (
        <GrantRoleDialog
          open={open}
          onOpenChange={onOpenChange}
          roles={roles}
          rolesLoading={assignments.rolesLoading}
          rolesKnown={rolesKnown}
          rolesError={rolesError}
          onRetryRoles={onRetryRoles}
        />
      )}
    />
  );
}
