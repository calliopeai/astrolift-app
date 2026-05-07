"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { ShieldIcon, Trash2Icon, UserPlusIcon, UsersIcon } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { REVOKE_ROLE_BINDING } from "@/graphql/identity/identity.mutations";
import {
  LIST_MEMBERS,
  LIST_ROLE_BINDINGS,
  LIST_ROLES,
} from "@/graphql/identity/identity.queries";
import type {
  AstroliftMember,
  AstroliftRole,
  AstroliftRoleBinding,
  MutationResult,
} from "@/graphql/identity/identity.types";

import { GrantRoleDialog } from "./grant-role-dialog";

interface MembersResp {
  astroliftMembers: AstroliftMember[];
}
interface RoleBindingsResp {
  astroliftRoleBindings: AstroliftRoleBinding[];
}
interface RolesResp {
  astroliftRoles: AstroliftRole[];
}

const scopeBadge: Record<string, string> = {
  ORG: "bg-blue-500/15 text-blue-700 dark:text-blue-300",
  TEAM: "bg-purple-500/15 text-purple-700 dark:text-purple-300",
  PROJECT: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  APP: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
};

export function MembersClient() {
  const [open, setOpen] = React.useState(false);
  const members = useQuery<MembersResp>(LIST_MEMBERS);
  const bindings = useQuery<RoleBindingsResp>(LIST_ROLE_BINDINGS);
  const roles = useQuery<RolesResp>(LIST_ROLES);

  const [revokeBinding, { loading: revoking }] = useMutation<{
    revokeRoleBinding: MutationResult<{ id: string; deleted: boolean }>;
  }>(REVOKE_ROLE_BINDING, {
    refetchQueries: [{ query: LIST_ROLE_BINDINGS }],
    awaitRefetchQueries: true,
  });

  async function handleRevoke(rb: AstroliftRoleBinding) {
    if (!confirm(`Revoke ${rb.role.slug} from ${rb.user?.username ?? rb.groupExternalId}?`)) {
      return;
    }
    const { data } = await revokeBinding({ variables: { input: { id: rb.id } } });
    if (data?.revokeRoleBinding.ok) {
      toast.success("Role revoked");
    } else {
      toast.error(data?.revokeRoleBinding.errors?.[0]?.message ?? "Revoke failed");
    }
  }

  const memberList = members.data?.astroliftMembers ?? [];
  const bindingList = bindings.data?.astroliftRoleBindings ?? [];

  // Group bindings by user for the role-binding tab.
  const bindingsByUser = new Map<string, AstroliftRoleBinding[]>();
  for (const b of bindingList) {
    if (!b.user) continue;
    const arr = bindingsByUser.get(b.user.id) ?? [];
    arr.push(b);
    bindingsByUser.set(b.user.id, arr);
  }

  return (
    <PageShell
      title="Members"
      description="Users with access to this organization, plus the role bindings that grant their permissions."
      actions={
        <Button onClick={() => setOpen(true)} disabled={roles.loading}>
          <UserPlusIcon className="size-4" />
          Grant role
        </Button>
      }
    >
      <Card>
        <CardHeader>
          <CardTitle>People</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {members.loading ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : memberList.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<UsersIcon className="size-5" />}
                title="No members"
                description="Members appear here once role bindings are granted to users."
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>User</TableHead>
                  <TableHead>Scope</TableHead>
                  <TableHead>Roles</TableHead>
                  <TableHead>Lifecycle</TableHead>
                  <TableHead>Joined</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {memberList.map((m) => {
                  const userBindings = bindingsByUser.get(m.user.id) ?? [];
                  return (
                    <TableRow key={m.id}>
                      <TableCell>
                        <div className="font-medium">{m.user.username}</div>
                        <div className="text-muted-foreground text-xs">{m.user.email}</div>
                      </TableCell>
                      <TableCell>
                        <Badge className={scopeBadge[m.scopeKind]} variant="secondary">
                          {m.scopeKind}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <div className="flex flex-wrap gap-1">
                          {userBindings.length === 0 ? (
                            <span className="text-muted-foreground text-xs">—</span>
                          ) : (
                            userBindings.map((b) => (
                              <Badge key={b.id} variant="outline" className="font-mono text-xs">
                                {b.role.slug}
                              </Badge>
                            ))
                          )}
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge variant={m.isActive ? "default" : "secondary"}>
                          {m.lifecycle}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-muted-foreground text-sm">
                        {m.joinedAt
                          ? new Date(m.joinedAt).toLocaleDateString()
                          : new Date(m.createdAt).toLocaleDateString()}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <CardTitle>Role bindings</CardTitle>
          <span className="text-muted-foreground text-xs">
            {bindingList.length} binding{bindingList.length === 1 ? "" : "s"}
          </span>
        </CardHeader>
        <CardContent className="p-0">
          {bindings.loading ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : bindingList.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<ShieldIcon className="size-5" />}
                title="No role bindings"
                description="Grant a system role to a user to give them access to the platform."
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Subject</TableHead>
                  <TableHead>Role</TableHead>
                  <TableHead>Scope</TableHead>
                  <TableHead>Granted</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {bindingList.map((b) => (
                  <TableRow key={b.id}>
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
                      <div className="text-muted-foreground font-mono text-xs">
                        {b.role.slug}
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge className={scopeBadge[b.scopeKind]} variant="secondary">
                        {b.scopeKind}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground text-sm">
                      {new Date(b.grantedAt).toLocaleDateString()}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => handleRevoke(b)}
                        disabled={revoking}
                      >
                        <Trash2Icon className="size-4" />
                        <span className="sr-only">Revoke</span>
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <GrantRoleDialog
        open={open}
        onOpenChange={setOpen}
        roles={roles.data?.astroliftRoles ?? []}
      />
    </PageShell>
  );
}
