"use client";

import { AssignmentsView } from "@/components/screens/administration/permissions/AssignmentsView";
import { useAssignments } from "@/components/screens/administration/permissions/use-assignments";

import { GrantRoleDialog } from "@/app/(app)/members/grant-role-dialog";

/** Mounted only while the Assignments tab is shown, so its queries run only then. */
export function AssignmentsTab() {
  const { roles, ...assignments } = useAssignments();
  return (
    <AssignmentsView
      {...assignments}
      renderGrantDialog={({ open, onOpenChange }) => (
        <GrantRoleDialog open={open} onOpenChange={onOpenChange} roles={roles} />
      )}
    />
  );
}
