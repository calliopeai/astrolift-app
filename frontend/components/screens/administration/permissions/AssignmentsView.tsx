"use client";

import { ShieldIcon, Trash2Icon, UserPlusIcon } from "lucide-react";
import * as React from "react";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { Column, RowSelection } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import type { AstroliftRoleBinding } from "@/graphql/identity/identity.types";
import { useFormatters } from "@/lib/i18n/formatters";

import { permissionsCrumbs } from "../access/admin-crumbs";
import { SCOPE_TONE } from "./scope-tone";
import type { useAssignments } from "./use-assignments";

export type AssignmentsViewProps = Omit<ReturnType<typeof useAssignments>, "roles"> & {
  /**
   * The grant-role dialog, rendered by the caller (it runs its own
   * queries). The view owns whether it is open; closing it also refetches
   * this list's page.
   */
  renderGrantDialog?: (props: {
    open: boolean;
    onOpenChange: (open: boolean) => void;
  }) => React.ReactNode;
};

function subjectLabel(b: AstroliftRoleBinding): string {
  return b.user?.username ?? `group:${b.groupExternalId}`;
}

/** Admin › Permissions › Assignments: role bindings (spec 44 §5.1), grant and revoke. */
export function AssignmentsView({
  list,
  page,
  canManage,
  rolesLoading,
  revoking,
  bulkRevoking,
  onRevoke,
  onBulkRevoke,
  renderGrantDialog,
}: AssignmentsViewProps) {
  const fmt = useFormatters();

  const [grantOpen, setGrantOpen] = React.useState(false);
  const [revokeTarget, setRevokeTarget] = React.useState<AstroliftRoleBinding | null>(null);
  const [bulkTarget, setBulkTarget] = React.useState<RowSelection | null>(null);
  const bulkCount = bulkTarget?.selectedCount ?? 0;

  async function handleBulkRevoke() {
    if (!bulkTarget) return;
    try {
      if (await onBulkRevoke(bulkTarget.selectedIds)) bulkTarget.clear();
    } finally {
      setBulkTarget(null);
    }
  }

  const columns: Column<AstroliftRoleBinding>[] = [
    {
      id: "user",
      header: "User",
      cellClassName: "max-w-72",
      cell: (b) =>
        b.user ? (
          <div className="min-w-0">
            <div className="truncate font-medium" title={b.user.username}>
              {b.user.username}
            </div>
            <div className="text-muted-foreground truncate font-mono text-xs" title={b.user.email}>
              {b.user.email}
            </div>
          </div>
        ) : (
          <div className="truncate font-mono text-xs" title={`group:${b.groupExternalId}`}>
            group:{b.groupExternalId}
          </div>
        ),
    },
    {
      id: "role",
      header: "Role",
      cellClassName: "max-w-72",
      cell: (b) => (
        <div className="min-w-0">
          <div className="truncate font-medium" title={b.role.name}>
            {b.role.name}
          </div>
          <div className="text-muted-foreground truncate font-mono text-xs" title={b.role.slug}>
            {b.role.slug}
          </div>
        </div>
      ),
    },
    {
      id: "scope",
      header: "Scope",
      cellClassName: "max-w-64",
      cell: (b) => (
        <div className="flex min-w-0 flex-col items-start gap-1">
          <Badge className={`${SCOPE_TONE[b.scopeKind] ?? ""} font-mono`} variant="secondary">
            {b.scopeKind}
          </Badge>
          {b.sourceScopeLabel && (
            <span
              className="text-muted-foreground block max-w-full truncate text-xs"
              title={b.sourceScopeLabel}
            >
              {b.sourceScopeLabel}
            </span>
          )}
        </div>
      ),
    },
    {
      id: "granted",
      header: "Granted",
      cellClassName: "text-muted-foreground font-mono text-xs",
      cell: (b) => fmt.formatDate(b.grantedAt),
    },
  ];

  return (
    <div className="flex min-w-0 flex-1 flex-col p-6">
      <ListPage<AstroliftRoleBinding>
        header={{
          crumbs: permissionsCrumbs("assignments"),
          title: "Assignments",
          context: "Every role granted to a user or IdP group, and the scope it applies to.",
          primaryAction: (
            <Can permission="org.manage_members">
              <Button size="sm" onClick={() => setGrantOpen(true)} disabled={rolesLoading}>
                <UserPlusIcon className="size-4" />
                Grant role
              </Button>
            </Can>
          ),
        }}
        list={list}
        label="Role bindings"
        columns={columns}
        rows={page.rows}
        getRowId={(b) => b.id}
        loading={page.loading}
        stale={page.stale}
        error={page.error}
        onRetry={page.refetch}
        totalCount={page.totalCount}
        nextCursor={page.nextCursor}
        rowActions={
          canManage
            ? (b) => (
                <DropdownMenuItem
                  variant="destructive"
                  disabled={revoking || bulkRevoking}
                  onSelect={() => setRevokeTarget(b)}
                >
                  <Trash2Icon className="size-4" />
                  Revoke
                </DropdownMenuItem>
              )
            : undefined
        }
        bulkActions={
          canManage
            ? (sel) => (
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={() => setBulkTarget(sel)}
                  disabled={bulkRevoking}
                >
                  <Trash2Icon className="size-4" />
                  Revoke {sel.selectedCount}
                </Button>
              )
            : undefined
        }
        empty={{
          icon: <ShieldIcon className="size-5" />,
          title: "No role bindings",
          description: "Grant a role to a user to give them access to the platform.",
        }}
      />

      <ConfirmDialog
        open={bulkTarget !== null}
        onOpenChange={(next) => {
          if (!next) setBulkTarget(null);
        }}
        title={`Revoke ${bulkCount} role binding${bulkCount === 1 ? "" : "s"}?`}
        description="The selected users lose the permissions these roles granted. Any other bindings they hold stay in effect."
        confirmLabel={`Revoke ${bulkCount}`}
        destructive
        onConfirm={handleBulkRevoke}
      />

      <ConfirmDialog
        open={revokeTarget !== null}
        onOpenChange={(next) => {
          if (!next) setRevokeTarget(null);
        }}
        title={
          revokeTarget
            ? `Revoke ${revokeTarget.role.slug} from ${subjectLabel(revokeTarget)}?`
            : "Revoke?"
        }
        description="The user loses the permissions this role granted. Other role bindings, if any, remain in effect."
        confirmLabel="Revoke"
        destructive
        onConfirm={async () => {
          if (revokeTarget) await onRevoke(revokeTarget);
        }}
      />

      {renderGrantDialog?.({
        open: grantOpen,
        onOpenChange: (next) => {
          setGrantOpen(next);
          // GrantRoleDialog refetches the deprecated flat list and exposes
          // no onGranted hook, so pull this list's page again when it
          // closes; otherwise a new binding would not appear until the
          // next fetch.
          if (!next) page.refetch();
        },
      })}
    </div>
  );
}
