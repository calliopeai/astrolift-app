"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { ShieldIcon, Trash2Icon, UserPlusIcon } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import {
  DataTable,
  useCursorTable,
  useRowSelection,
  type Column,
  type CursorPage,
} from "@/components/data-table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";
import {
  BULK_REVOKE_ROLE_BINDINGS,
  REVOKE_ROLE_BINDING,
} from "@/graphql/identity/identity.mutations";
import {
  LIST_ROLE_BINDINGS,
  LIST_ROLE_BINDINGS_PAGE,
  LIST_ROLES,
} from "@/graphql/identity/identity.queries";
import type {
  AstroliftBulkRevokeRoleBindingsPayload,
  AstroliftRole,
  AstroliftRoleBinding,
  MutationResult,
} from "@/graphql/identity/identity.types";
import { useFormatters } from "@/lib/i18n/formatters";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import { GrantRoleDialog } from "@/app/(app)/members/grant-role-dialog";

interface BindingsPageResp {
  astroliftRoleBindingsPage: CursorPage<AstroliftRoleBinding>;
}
interface RolesResp {
  astroliftRoles: AstroliftRole[];
}

const SCOPE_TONE: Record<string, string> = {
  ORG: "bg-info/15 text-info-fg",
  TEAM: "bg-chart-3/15 text-chart-3",
  PROJECT: "bg-success/15 text-success-fg",
  APP: "bg-warning/15 text-warning-fg",
};

/**
 * Both revoke paths refresh two documents: this table's paginated one by
 * operation name, so it re-runs with the cursor and search currently in
 * effect, and the deprecated flat list that /members, the member detail
 * page and the diagnostics tab still read from the cache.
 */
const REVOKE_REFETCH = ["ListRoleBindingsPage", { query: LIST_ROLE_BINDINGS }];

function subjectLabel(b: AstroliftRoleBinding): string {
  return b.user?.username ?? `group:${b.groupExternalId}`;
}

export function AssignmentsTab() {
  const fmt = useFormatters();
  const perms = useMyPermissions();
  const canManage = perms.can("org.manage_members");

  // `astroliftRoleBindingsPage` searches username, email, first / last
  // name, group external id and role slug / name. It takes no sort
  // argument, so no column declares a `sortKey`.
  const table = useCursorTable<AstroliftRoleBinding>({
    query: LIST_ROLE_BINDINGS_PAGE,
    extract: (d) => (d as BindingsPageResp | undefined)?.astroliftRoleBindingsPage,
    searchVariable: "search",
    urlKey: "rb",
    fetchPolicy: "cache-and-network",
  });
  const selection = useRowSelection();

  const roles = useQuery<RolesResp>(LIST_ROLES);

  const [grantOpen, setGrantOpen] = React.useState(false);
  const [revokeTarget, setRevokeTarget] = React.useState<AstroliftRoleBinding | null>(null);
  const [confirmBulk, setConfirmBulk] = React.useState(false);

  const [revokeBinding, { loading: revoking }] = useMutation<{
    revokeRoleBinding: MutationResult<{ id: string; deleted: boolean }>;
  }>(REVOKE_ROLE_BINDING, {
    refetchQueries: REVOKE_REFETCH,
    awaitRefetchQueries: true,
  });
  const [bulkRevoke, { loading: bulkRevoking }] = useMutation<{
    bulkRevokeAstroliftRoleBindings: MutationResult<AstroliftBulkRevokeRoleBindingsPayload>;
  }>(BULK_REVOKE_ROLE_BINDINGS, {
    refetchQueries: REVOKE_REFETCH,
    awaitRefetchQueries: true,
  });

  async function handleRevoke(b: AstroliftRoleBinding) {
    const { data } = await revokeBinding({ variables: { input: { id: b.id } } });
    if (data?.revokeRoleBinding.ok) {
      toast.success("Role revoked");
    } else {
      throw new Error(data?.revokeRoleBinding.errors?.[0]?.message ?? "Revoke failed");
    }
  }

  async function handleBulkRevoke() {
    const ids = selection.selectedIds;
    if (ids.length === 0) return;
    try {
      const { data } = await bulkRevoke({ variables: { input: { bindingIds: ids } } });
      const env = data?.bulkRevokeAstroliftRoleBindings;
      if (!env?.ok || !env.data) {
        toast.error(env?.errors?.[0]?.message ?? "Bulk revoke failed");
        return;
      }
      const { revokedCount, failedCount } = env.data;
      if (failedCount === 0) {
        toast.success(`Revoked ${revokedCount} binding${revokedCount === 1 ? "" : "s"}`);
      } else {
        toast.warning(`Revoked ${revokedCount}, ${failedCount} failed`);
      }
      selection.clear();
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
            <Button size="sm" onClick={() => setGrantOpen(true)} disabled={roles.loading}>
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
          if (revokeTarget) await handleRevoke(revokeTarget);
        }}
      />

      <GrantRoleDialog
        open={grantOpen}
        onOpenChange={(next) => {
          setGrantOpen(next);
          // GrantRoleDialog refetches the deprecated flat list and exposes
          // no onGranted hook, so pull this table's page again when it
          // closes; otherwise a new binding would not appear until the
          // next fetch.
          if (!next) table.refetch();
        }}
        roles={roles.data?.astroliftRoles ?? []}
      />
    </>
  );
}
