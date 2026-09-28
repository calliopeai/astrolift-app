"use client";

import { LockIcon, PlusIcon, ShieldIcon } from "lucide-react";
import * as React from "react";

import { Can } from "@/components/Can";
import { DataTable, type Column } from "@/components/data-table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";
import type { AstroliftRole } from "@/graphql/identity/identity.types";

import { RoleEditorSheet, type EditorMode } from "./RoleEditorSheet";
import { SCOPE_TONE } from "./scope-tone";
import type { useRoles } from "./use-roles";

export type RolesViewProps = ReturnType<typeof useRoles>;

/** The Roles tab of the Permissions screen: the roles table and its editor sheet. */
export function RolesView({
  table,
  allPermissions,
  canManage,
  createRole,
  updateRole,
  saving,
}: RolesViewProps) {
  const columns: Column<AstroliftRole>[] = [
    {
      id: "role",
      header: "Role",
      cell: (role) => (
        <>
          <div className="font-medium">{role.name}</div>
          <div className="text-muted-foreground font-mono text-xs">{role.slug}</div>
        </>
      ),
    },
    {
      id: "scope",
      header: "Scope",
      cell: (role) => (
        <Badge className={SCOPE_TONE[role.scopeLevel]} variant="secondary">
          {role.scopeLevel}
        </Badge>
      ),
    },
    {
      id: "type",
      header: "Type",
      cell: (role) =>
        role.isSystem ? (
          <Badge variant="outline" className="gap-1">
            <LockIcon className="size-3" />
            System
          </Badge>
        ) : (
          <Badge variant="secondary">Custom</Badge>
        ),
    },
    {
      id: "permissions",
      header: "Permissions",
      align: "right",
      cellClassName: "font-mono tabular-nums",
      cell: (role) => role.permissions.length,
    },
  ];

  const [sheetOpen, setSheetOpen] = React.useState(false);
  const [sheetMode, setSheetMode] = React.useState<EditorMode>("create");
  // In edit mode this is the role being edited; in create mode it is an
  // optional seed to clone from (null = blank new role).
  const [sheetRole, setSheetRole] = React.useState<AstroliftRole | null>(null);

  function openCreate() {
    setSheetMode("create");
    setSheetRole(null);
    setSheetOpen(true);
  }
  function openRole(role: AstroliftRole) {
    setSheetMode("edit");
    setSheetRole(role);
    setSheetOpen(true);
  }
  function cloneRole(role: AstroliftRole) {
    // Switch the open sheet from a read-only system role into a fresh
    // create form seeded with that role's permissions — the "clone and
    // prune" path the backend documents for system roles.
    setSheetMode("create");
    setSheetRole(role);
  }

  return (
    <>
      <Section
        title="Roles"
        description="A role is a named permission set. System roles ship with the platform and are read-only; create custom roles to tailor access."
        action={
          <Can permission="org.manage_members">
            <Button size="sm" onClick={openCreate}>
              <PlusIcon className="size-4" />
              New role
            </Button>
          </Can>
        }
      >
        <DataTable
          label="Roles"
          controller={table}
          columns={columns}
          getRowId={(role) => role.id}
          // A role has no page of its own; activating one opens the editor
          // sheet, read-only for a system role or a viewer without manage.
          onRowActivate={openRole}
          rowLabel={(role) => `${role.isSystem || !canManage ? "View" : "Edit"} role ${role.name}`}
          searchPlaceholder="Search roles by name or slug…"
          empty={{
            icon: <ShieldIcon className="size-5" />,
            title: "No roles",
            description:
              "System roles are seeded on deploy; if none appear, the org context may not be resolved yet.",
          }}
          emptyFiltered={{
            title: "No matching roles",
            description: "No role matches this search. Try another name or slug.",
          }}
        />
      </Section>

      <RoleEditorSheet
        open={sheetOpen}
        onOpenChange={setSheetOpen}
        mode={sheetMode}
        role={sheetRole}
        allPermissions={allPermissions}
        canManage={canManage}
        onClone={cloneRole}
        saving={saving}
        onCreate={createRole}
        onUpdate={updateRole}
      />
    </>
  );
}
