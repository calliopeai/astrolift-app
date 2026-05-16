"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { ShieldIcon, Trash2Icon, UsersIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
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
  LIST_ROLES,
  LIST_ROLE_BINDINGS,
} from "@/graphql/identity/identity.queries";
import type {
  AstroliftRole,
  AstroliftRoleBinding,
  MutationResult,
} from "@/graphql/identity/identity.types";
import { GET_APP } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

interface AppResp {
  astroliftApp: AstroliftRegisteredApp | null;
}
interface BindingsResp {
  astroliftRoleBindings: AstroliftRoleBinding[];
}
interface RolesResp {
  astroliftRoles: AstroliftRole[];
}

export function AppMembersClient({ slug }: { slug: string }) {
  const tCommon = useTranslations("apps.common");
  const t = useTranslations("apps.members");
  const app = useQuery<AppResp>(GET_APP, { variables: { slug } });
  const bindings = useQuery<BindingsResp>(LIST_ROLE_BINDINGS);
  const roles = useQuery<RolesResp>(LIST_ROLES);

  const [revoke, { loading: revoking }] = useMutation<{
    revokeRoleBinding: MutationResult<{ id: string; deleted: boolean }>;
  }>(REVOKE_ROLE_BINDING, {
    refetchQueries: [{ query: LIST_ROLE_BINDINGS }],
    awaitRefetchQueries: true,
  });

  const [revokeTarget, setRevokeTarget] = React.useState<AstroliftRoleBinding | null>(null);

  const a = app.data?.astroliftApp ?? null;
  const allBindings = bindings.data?.astroliftRoleBindings ?? [];
  const roleList = roles.data?.astroliftRoles ?? [];

  // Filter to bindings scoped to this app. The schema doesn't have an
  // app-scoped query yet, so we client-filter the org-wide list.
  const appBindings = React.useMemo(() => {
    if (!a) return [];
    return allBindings.filter(
      (rb) => rb.scopeKind === "APP" && rb.scopeId === a.id,
    );
  }, [allBindings, a]);

  // Roles relevant to APP scope.
  const appRoles = roleList.filter((r) => r.scopeLevel === "APP");

  async function handleRevoke(rb: AstroliftRoleBinding) {
    const { data } = await revoke({ variables: { input: { id: rb.id } } });
    if (data?.revokeRoleBinding.ok) {
      toast.success("Role revoked");
    } else {
      throw new Error(data?.revokeRoleBinding.errors?.[0]?.message ?? "Revoke failed");
    }
  }

  if (app.loading && !a) {
    return (
      <PageShell title={t("loadingTitle")} description={tCommon("loading")}>
        <Skeleton className="h-32 w-full" />
      </PageShell>
    );
  }

  if (!a) {
    return (
      <PageShell
        title={tCommon("notFound")}
        description={tCommon("notFoundPermission")}
      >
        <EmptyState
          icon={<UsersIcon className="size-5" />}
          title={tCommon("notFoundSlug", { slug })}
          actionHref="/apps"
          actionLabel={tCommon("backToApps")}
        />
      </PageShell>
    );
  }

  return (
    <PageShell
      title={t("title")}
      description={t("description", { slug: a.slug })}
    >
      <Card>
        <CardContent className="p-4">
          <p className="text-muted-foreground text-xs uppercase tracking-wide">
            {t("availableRoles")}
          </p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {appRoles.map((r) => (
              <Badge key={r.id} variant="outline" className="font-mono text-[10px]">
                {r.slug}
              </Badge>
            ))}
            {appRoles.length === 0 && (
              <span className="text-muted-foreground text-xs">
                {t("noRolesDefined")}
              </span>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="p-0">
          {bindings.loading && appBindings.length === 0 ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : appBindings.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<ShieldIcon className="size-5" />}
                title={t("emptyTitle")}
                description={t("emptyDescription")}
                actionHref="/members"
                actionLabel={t("openMembers")}
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("columns.user")}</TableHead>
                  <TableHead>{t("columns.role")}</TableHead>
                  <TableHead>{t("columns.granted")}</TableHead>
                  <TableHead>{t("columns.expires")}</TableHead>
                  <TableHead className="text-right"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {appBindings.map((rb) => (
                  <TableRow key={rb.id}>
                    <TableCell>
                      {rb.user ? (
                        <>
                          <div className="font-medium">{rb.user.username}</div>
                          <div className="text-muted-foreground text-xs">
                            {rb.user.email}
                          </div>
                        </>
                      ) : (
                        <span className="font-mono text-xs">
                          {t("groupPrefix")} {rb.groupExternalId}
                        </span>
                      )}
                    </TableCell>
                    <TableCell>
                      <Badge variant="secondary" className="font-mono text-[10px]">
                        {rb.role.slug}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground text-xs">
                      {new Date(rb.grantedAt).toLocaleDateString()}
                    </TableCell>
                    <TableCell className="text-muted-foreground text-xs">
                      {rb.expiresAt ? new Date(rb.expiresAt).toLocaleDateString() : t("never")}
                    </TableCell>
                    <TableCell className="text-right">
                      <Can permission="org.manage_members">
                        <Button
                          variant="ghost"
                          size="icon"
                          className="size-8"
                          onClick={() => setRevokeTarget(rb)}
                          disabled={revoking}
                        >
                          <Trash2Icon className="size-4" />
                          <span className="sr-only">{t("revoke")}</span>
                        </Button>
                      </Can>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <ConfirmDialog
        open={revokeTarget !== null}
        onOpenChange={(next) => {
          if (!next) setRevokeTarget(null);
        }}
        title={
          revokeTarget
            ? t("revokeConfirm.title", {
                role: revokeTarget.role.slug,
                target: revokeTarget.user?.username ?? revokeTarget.groupExternalId,
              })
            : t("revokeConfirm.fallbackTitle")
        }
        description={t("revokeConfirm.description")}
        confirmLabel={t("revokeConfirm.confirm")}
        destructive
        onConfirm={async () => {
          if (revokeTarget) await handleRevoke(revokeTarget);
        }}
      />
    </PageShell>
  );
}
