"use client";

import { ShieldIcon, Trash2Icon, UserPlusIcon } from "lucide-react";
import * as React from "react";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DataTable, type Column } from "@/components/data-table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";
import type { AstroliftRoleBinding } from "@/graphql/identity/identity.types";
import { useFormatters } from "@/lib/i18n/formatters";

import { SCOPE_TONE } from "./scope-tone";
import type { useAssignments } from "./use-assignments";

export type AssignmentsViewProps = Omit<ReturnType<typeof useAssignments>, "roles"> & {
  /**
   * The grant-role dialog, rendered by the caller (it runs its own
   * queries). The view owns whether it is open; closing it also refetches
   * this table's page.
   */
  renderGrantDialog?: (props: {
    open: boolean;
    onOpenChange: (open: boolean) => void;
  }) => React.ReactNode;
};

function subjectLabel(b: AstroliftRoleBinding): string {
  return b.user?.username ?? `group:${b.groupExternalId}`;
}

/** The Assignments tab of the Permissions screen: role bindings, grant and revoke. */
export function AssignmentsView({
  table,
  selection,
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
  const [confirmBulk, setConfirmBulk] = React.useState(false);

  async function handleBulkRevoke() {
    try {
      await onBulkRevoke();
    } finally {
      setConfirmBulk(false);
    }
  }

  const columns: Column<AstroliftRoleBinding>[] = [
    {
      id: "user",
      header: "User",
      cell: (b) =>
        b.user ? (
          <>
            <div className="font-medium">{b.user.username}</div>
            <div className="text-muted-foreground text-xs">{b.user.email}</div>
          </>
        ) : (
          <div className="font-mono text-xs">group:{b.groupExternalId}</div>
        ),
    },
    {
      id: "role",
      header: "Role",
      cell: (b) => (
        <>
          <div className="font-medium">{b.role.name}</div>
          <div className="text-muted-foreground font-mono text-xs">{b.role.slug}</div>
        </>
      ),
    },
    {
      id: "scope",
      header: "Scope",
      cell: (b) => (
        <div className="flex flex-col items-start gap-1">
          <Badge className={SCOPE_TONE[b.scopeKind]} variant="secondary">
            {b.scopeKind}
          </Badge>
          {b.sourceScopeLabel && (
            <span className="text-muted-foreground text-xs">{b.sourceScopeLabel}</span>
          )}
        </div>
      ),
    },
    {
      id: "granted",
      header: "Granted",
      cellClassName: "text-muted-foreground text-sm",
      cell: (b) => fmt.formatDate(b.grantedAt),
    },
    {
      id: "actions",
      header: "Actions",
      align: "right",
      width: "w-24",
      cell: (b) => (
        <Can permission="org.manage_members">
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setRevokeTarget(b)}
            disabled={revoking || bulkRevoking}
          >
            <Trash2Icon className="size-4" />
            <span className="sr-only">
              Revoke {b.role.slug} from {subjectLabel(b)}
            </span>
          </Button>
        </Can>
      ),
    },
  ];

  return (
    <>
      <Section
        title="Role bindings"
        description="Every role granted to a user (or IdP group) and the scope it applies to. Grant, revoke, or bulk-revoke access here."
        action={
          <Can permission="org.manage_members">
            <Button size="sm" onClick={() => setGrantOpen(true)} disabled={rolesLoading}>
              <UserPlusIcon className="size-4" />
              Grant role
            </Button>
          </Can>
        }
      >
        <DataTable
          label="Role bindings"
          controller={table}
          columns={columns}
          getRowId={(b) => b.id}
          selection={canManage ? selection : undefined}
          bulkActions={
            canManage
              ? (sel) => (
                  <Button
                    variant="destructive"
                    size="sm"
                    onClick={() => setConfirmBulk(true)}
                    disabled={bulkRevoking}
                  >
                    <Trash2Icon className="size-4" />
                    Revoke {sel.selectedCount}
                  </Button>
                )
              : undefined
          }
          searchPlaceholder="Search by user, group, or role…"
          empty={{
            icon: <ShieldIcon className="size-5" />,
            title: "No role bindings",
            description: "Grant a role to a user to give them access to the platform.",
          }}
          emptyFiltered={{
            title: "No matching bindings",
            description: "No binding matches this search. Try another user, group, or role.",
          }}
        />
      </Section>

      <ConfirmDialog
        open={confirmBulk}
        onOpenChange={setConfirmBulk}
        title={`Revoke ${selection.selectedCount} role binding${selection.selectedCount === 1 ? "" : "s"}?`}
        description="The selected users lose the permissions these roles granted. Any other bindings they hold stay in effect."
        confirmLabel={`Revoke ${selection.selectedCount}`}
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
          // no onGranted hook, so pull this table's page again when it
          // closes; otherwise a new binding would not appear until the
          // next fetch.
          if (!next) table.refetch();
        },
      })}
    </>
  );
}
