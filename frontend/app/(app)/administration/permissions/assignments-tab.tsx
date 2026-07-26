"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { ShieldIcon, Trash2Icon, UserPlusIcon } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  BULK_REVOKE_ROLE_BINDINGS,
  REVOKE_ROLE_BINDING,
} from "@/graphql/identity/identity.mutations";
import { LIST_ROLE_BINDINGS, LIST_ROLES } from "@/graphql/identity/identity.queries";
import type {
  AstroliftBulkRevokeRoleBindingsPayload,
  AstroliftRole,
  AstroliftRoleBinding,
  MutationResult,
} from "@/graphql/identity/identity.types";
import { useFormatters } from "@/lib/i18n/formatters";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import { GrantRoleDialog } from "@/app/(app)/members/grant-role-dialog";

interface BindingsResp {
  astroliftRoleBindings: AstroliftRoleBinding[];
}
interface RolesResp {
  astroliftRoles: AstroliftRole[];
}

const SCOPE_TONE: Record<string, string> = {
  ORG: "bg-info/15 text-info-fg",
  TEAM: "bg-purple-500/15 text-purple-700 dark:text-purple-300",
  PROJECT: "bg-success/15 text-success-fg",
  APP: "bg-warning/15 text-warning-fg",
};

function subjectLabel(b: AstroliftRoleBinding): string {
  return b.user?.username ?? `group:${b.groupExternalId}`;
}

export function AssignmentsTab() {
  const fmt = useFormatters();
  const perms = useMyPermissions();
  const canManage = perms.can("org.manage_members");

  const bindings = useQuery<BindingsResp>(LIST_ROLE_BINDINGS, {
    fetchPolicy: "cache-and-network",
  });
  const roles = useQuery<RolesResp>(LIST_ROLES);

  const [grantOpen, setGrantOpen] = React.useState(false);
  const [revokeTarget, setRevokeTarget] = React.useState<AstroliftRoleBinding | null>(null);
  const [selected, setSelected] = React.useState<Set<string>>(() => new Set());
  const [confirmBulk, setConfirmBulk] = React.useState(false);

  const [revokeBinding, { loading: revoking }] = useMutation<{
    revokeRoleBinding: MutationResult<{ id: string; deleted: boolean }>;
  }>(REVOKE_ROLE_BINDING, {
    refetchQueries: [{ query: LIST_ROLE_BINDINGS }],
    awaitRefetchQueries: true,
  });
  const [bulkRevoke, { loading: bulkRevoking }] = useMutation<{
    bulkRevokeAstroliftRoleBindings: MutationResult<AstroliftBulkRevokeRoleBindingsPayload>;
  }>(BULK_REVOKE_ROLE_BINDINGS, {
    refetchQueries: [{ query: LIST_ROLE_BINDINGS }],
    awaitRefetchQueries: true,
  });

  const bindingList = bindings.data?.astroliftRoleBindings ?? [];

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }
  function toggleAll(ids: string[]) {
    setSelected((prev) => {
      const allOn = ids.length > 0 && ids.every((id) => prev.has(id));
      return allOn ? new Set() : new Set(ids);
    });
  }

  async function handleRevoke(b: AstroliftRoleBinding) {
    const { data } = await revokeBinding({ variables: { input: { id: b.id } } });
    if (data?.revokeRoleBinding.ok) {
      toast.success("Role revoked");
    } else {
      throw new Error(data?.revokeRoleBinding.errors?.[0]?.message ?? "Revoke failed");
    }
  }

  async function handleBulkRevoke() {
    const ids = Array.from(selected);
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
      setSelected(new Set());
    } finally {
      setConfirmBulk(false);
    }
  }

  return (
    <>
      <Section
        title="Role bindings"
        description="Every role granted to a user (or IdP group) and the scope it applies to. Grant, revoke, or bulk-revoke access here."
        action={
          <div className="flex items-center gap-2">
            <span className="text-muted-foreground text-xs">
              {bindingList.length} binding{bindingList.length === 1 ? "" : "s"}
            </span>
            <Can permission="org.manage_members">
              <Button size="sm" onClick={() => setGrantOpen(true)} disabled={roles.loading}>
                <UserPlusIcon className="size-4" />
                Grant role
              </Button>
            </Can>
          </div>
        }
      >
        {bindings.loading && bindingList.length === 0 ? (
          <div className="space-y-2">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        ) : bindingList.length === 0 ? (
          <EmptyState
            icon={<ShieldIcon className="size-5" />}
            title="No role bindings"
            description="Grant a role to a user to give them access to the platform."
          />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                {canManage && (
                  <TableHead className="w-10">
                    <input
                      type="checkbox"
                      aria-label="Select all bindings"
                      checked={
                        bindingList.length > 0 && bindingList.every((b) => selected.has(b.id))
                      }
                      onChange={() => toggleAll(bindingList.map((b) => b.id))}
                      className="size-4"
                      disabled={bulkRevoking}
                    />
                  </TableHead>
                )}
                <TableHead>User</TableHead>
                <TableHead>Role</TableHead>
                <TableHead>Scope</TableHead>
                <TableHead>Granted</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {bindingList.map((b) => (
                <TableRow key={b.id}>
                  {canManage && (
                    <TableCell>
                      <input
                        type="checkbox"
                        aria-label={`Select ${b.role.slug} for ${subjectLabel(b)}`}
                        checked={selected.has(b.id)}
                        onChange={() => toggle(b.id)}
                        className="size-4"
                        disabled={bulkRevoking}
                      />
                    </TableCell>
                  )}
                  <TableCell>
                    {b.user ? (
                      <>
                        <div className="font-medium">{b.user.username}</div>
                        <div className="text-muted-foreground text-xs">{b.user.email}</div>
                      </>
                    ) : (
                      <div className="font-mono text-xs">group:{b.groupExternalId}</div>
                    )}
                  </TableCell>
                  <TableCell>
                    <div className="font-medium">{b.role.name}</div>
                    <div className="text-muted-foreground font-mono text-xs">{b.role.slug}</div>
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-col items-start gap-1">
                      <Badge className={SCOPE_TONE[b.scopeKind]} variant="secondary">
                        {b.scopeKind}
                      </Badge>
                      {b.sourceScopeLabel && (
                        <span className="text-muted-foreground text-xs">{b.sourceScopeLabel}</span>
                      )}
                    </div>
                  </TableCell>
                  <TableCell className="text-muted-foreground text-sm">
                    {fmt.formatDate(b.grantedAt)}
                  </TableCell>
                  <TableCell className="text-right">
                    <Can permission="org.manage_members">
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => setRevokeTarget(b)}
                        disabled={revoking || bulkRevoking}
                      >
                        <Trash2Icon className="size-4" />
                        <span className="sr-only">Revoke</span>
                      </Button>
                    </Can>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Section>

      {canManage && selected.size > 0 && (
        <div className="bg-background pointer-events-auto fixed inset-x-0 bottom-0 z-30 border-t shadow-lg">
          <div className="mx-auto flex max-w-5xl flex-col items-stretch gap-2 p-3 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-sm font-medium">
              {selected.size} binding{selected.size === 1 ? "" : "s"} selected
            </p>
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
              <Button
                variant="ghost"
                onClick={() => setSelected(new Set())}
                disabled={bulkRevoking}
                className="min-h-11 w-full sm:w-auto"
              >
                Clear
              </Button>
              <Button
                variant="destructive"
                onClick={() => setConfirmBulk(true)}
                disabled={bulkRevoking}
                className="min-h-11 w-full sm:w-auto"
              >
                <Trash2Icon className="size-4" />
                Revoke {selected.size}
              </Button>
            </div>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={confirmBulk}
        onOpenChange={setConfirmBulk}
        title={`Revoke ${selected.size} role binding${selected.size === 1 ? "" : "s"}?`}
        description="The selected users lose the permissions these roles granted. Any other bindings they hold stay in effect."
        confirmLabel={`Revoke ${selected.size}`}
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
        onOpenChange={setGrantOpen}
        roles={roles.data?.astroliftRoles ?? []}
      />
    </>
  );
}
