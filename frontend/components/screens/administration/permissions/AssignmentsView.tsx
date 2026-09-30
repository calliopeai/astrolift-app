"use client";

import {
  DownloadIcon,
  MoreHorizontalIcon,
  ShieldIcon,
  Trash2Icon,
  UserPlusIcon,
} from "lucide-react";
import * as React from "react";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { RowSelection } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { AstroliftRoleBinding } from "@/graphql/identity/identity.types";
import { useFormatters } from "@/lib/i18n/formatters";

import { permissionsCrumbs } from "../access/admin-crumbs";
import { bindingColumns } from "./binding-columns";
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

/**
 * Admin › Permissions › Assignments: every role binding in the org (spec 44
 * §5.1), grant and revoke. It stays a list of its own: it is the only place
 * that shows every binding at once, IdP group mappings included, and the only
 * bulk revoke. One role's bindings are that role's Holders tab; a person's
 * are their Access tab (design 3.2). Design 5 folds this into People with
 * `has:binding` once People can filter on it.
 */
export function AssignmentsView({
  list,
  page,
  canManage,
  rolesLoading,
  revoking,
  bulkRevoking,
  onRevoke,
  onBulkRevoke,
  exportingCsv,
  onExportCsv,
  renderGrantDialog,
}: AssignmentsViewProps) {
  const fmt = useFormatters();

  const [grantOpen, setGrantOpen] = React.useState(false);
  const [revokeTarget, setRevokeTarget] = React.useState<AstroliftRoleBinding | null>(null);
  const [bulkTarget, setBulkTarget] = React.useState<RowSelection | null>(null);
  const bulkCount = bulkTarget?.selectedCount ?? 0;

  async function handleBulkRevoke() {
    if (!bulkTarget) return;
    if (await onBulkRevoke(bulkTarget.selectedIds)) bulkTarget.clear();
  }

  const columns = bindingColumns({ formatDate: fmt.formatDate });

  return (
    <div className="flex min-w-0 flex-1 flex-col p-6">
      <ListPage<AstroliftRoleBinding>
        header={{
          crumbs: permissionsCrumbs("assignments"),
          title: "Assignments",
          context: "Every role held by a user or an IdP group, where it applies, and why.",
          primaryAction: (
            <Can permission="org.manage_members">
              <Button size="sm" onClick={() => setGrantOpen(true)} disabled={rolesLoading}>
                <UserPlusIcon className="size-4" />
                Grant role
              </Button>
            </Can>
          ),
        }}
        menu={
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" className="size-8" aria-label="More list actions">
                <MoreHorizontalIcon className="size-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem
                disabled={exportingCsv || page.totalCount === 0}
                onSelect={() => {
                  void onExportCsv();
                }}
              >
                <DownloadIcon className="size-4" />
                {exportingCsv ? "Exporting CSV…" : "Export matching assignments as CSV"}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        }
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
