"use client";

import { LockIcon, PlusIcon, ShieldIcon } from "lucide-react";
import * as React from "react";

import { Can } from "@/components/Can";
import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { AstroliftRole } from "@/graphql/identity/identity.types";

import { permissionsCrumbs } from "../access/admin-crumbs";
import { RoleEditorSheet, type EditorMode } from "./RoleEditorSheet";
import { SCOPE_TONE } from "./scope-tone";
import type { useRoles } from "./use-roles";

export type RolesViewProps = ReturnType<typeof useRoles>;

/** Admin › Permissions › Roles: the roles list (spec 44 §5.1) and its editor sheet. */
export function RolesView({
  list,
  page,
  allPermissions,
  canManage,
  createRole,
  updateRole,
  saving,
}: RolesViewProps) {
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

  const columns: Column<AstroliftRole>[] = [
    {
      id: "role",
      header: "Role",
      cellClassName: "max-w-96",
      // A role has no page of its own: its name opens the editor sheet,
      // read-only for a system role or a viewer without manage.
      cell: (role) => (
        <button
          type="button"
          onClick={() => openRole(role)}
          aria-label={`${role.isSystem || !canManage ? "View" : "Edit"} role ${role.name}`}
          className="focus-visible:ring-ring block w-full min-w-0 rounded-sm text-left focus-visible:ring-2 focus-visible:outline-none"
        >
          <span className="block truncate font-medium hover:underline" title={role.name}>
            {role.name}
          </span>
          <span
            className="text-muted-foreground block truncate font-mono text-xs"
            title={role.slug}
          >
            {role.slug}
          </span>
        </button>
      ),
    },
    {
      id: "scope",
      header: "Scope",
      cell: (role) => (
        <Badge className={`${SCOPE_TONE[role.scopeLevel] ?? ""} font-mono`} variant="secondary">
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

  return (
    <div className="flex min-w-0 flex-1 flex-col p-6">
      <ListPage<AstroliftRole>
        header={{
          crumbs: permissionsCrumbs("roles"),
          title: "Roles",
          context: "Named permission sets. System roles are read-only; custom roles tailor access.",
          primaryAction: (
            <Can permission="org.manage_members">
              <Button size="sm" onClick={openCreate}>
                <PlusIcon className="size-4" />
                New role
              </Button>
            </Can>
          ),
        }}
        list={list}
        label="Roles"
        columns={columns}
        rows={page.rows}
        getRowId={(role) => role.id}
        loading={page.loading}
        stale={page.stale}
        error={page.error}
        onRetry={page.refetch}
        totalCount={page.totalCount}
        nextCursor={page.nextCursor}
        empty={{
          icon: <ShieldIcon className="size-5" />,
          title: "No roles",
          description:
            "System roles are seeded on deploy; if none appear, the org context may not be resolved yet.",
        }}
      />

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
    </div>
  );
}
