"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { ShieldIcon, Trash2Icon, UsersIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import {
  DataTable,
  useCursorTable,
  type Column,
  type CursorPage,
} from "@/components/data-table";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { REVOKE_ROLE_BINDING } from "@/graphql/identity/identity.mutations";
import {
  LIST_ROLES,
  LIST_ROLE_BINDINGS,
  LIST_ROLE_BINDINGS_PAGE,
} from "@/graphql/identity/identity.queries";
import type {
  AstroliftRole,
  AstroliftRoleBinding,
  MutationResult,
} from "@/graphql/identity/identity.types";
import { GET_APP } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

import { AppTabs } from "../components/app-tabs";

interface AppResp {
  astroliftApp: AstroliftRegisteredApp | null;
}
interface BindingsPageResp {
  astroliftRoleBindingsPage: CursorPage<AstroliftRoleBinding>;
}
interface RolesResp {
  astroliftRoles: AstroliftRole[];
}

export function AppMembersClient({ slug }: { slug: string }) {
  const tCommon = useTranslations("apps.common");
  const t = useTranslations("apps.members");
  const app = useQuery<AppResp>(GET_APP, { variables: { slug } });
  const roles = useQuery<RolesResp>(LIST_ROLES);

  // `appSlug` narrows server-side to `scope_kind=APP, scope_id=<this app>`,
  // which is exactly what this page filtered for in the browser. The
  // argument was added to the field for this surface (#1241) and no
  // document had declared it, so the page went on fetching every binding
  // in the org to keep a handful. The field takes no sort argument, so no
  // column declares a `sortKey`.
  const table = useCursorTable<AstroliftRoleBinding>({
    query: LIST_ROLE_BINDINGS_PAGE,
    variables: { appSlug: slug },
    extract: (d) => (d as BindingsPageResp | undefined)?.astroliftRoleBindingsPage,
    searchVariable: "search",
    urlKey: "mem",
    fetchPolicy: "cache-and-network",
  });

  const [revoke, { loading: revoking }] = useMutation<{
    revokeRoleBinding: MutationResult<{ id: string; deleted: boolean }>;
  }>(REVOKE_ROLE_BINDING, {
    // The paginated document by operation name, so the revoke lands on the
    // cursor and search in effect, plus the deprecated flat list that
    // /members and the member detail page still read from the cache.
    refetchQueries: ["ListRoleBindingsPage", { query: LIST_ROLE_BINDINGS }],
    awaitRefetchQueries: true,
  });

  const [revokeTarget, setRevokeTarget] = React.useState<AstroliftRoleBinding | null>(null);

  const a = app.data?.astroliftApp ?? null;

  // Roles relevant to APP scope.
  const appRoles = (roles.data?.astroliftRoles ?? []).filter((r) => r.scopeLevel === "APP");

  const columns: Column<AstroliftRoleBinding>[] = [
    {
      id: "user",
      header: t("columns.user"),
      cell: (rb) =>
        rb.user ? (
          <>
            <div className="font-medium">{rb.user.username}</div>
            <div className="text-muted-foreground text-xs">{rb.user.email}</div>
          </>
        ) : (
          <span className="font-mono text-xs">
            {t("groupPrefix")} {rb.groupExternalId}
          </span>
        ),
    },
    {
      id: "role",
      header: t("columns.role"),
      cell: (rb) => (
        <Badge variant="secondary" className="font-mono text-2xs">
          {rb.role.slug}
        </Badge>
      ),
    },
    {
      id: "granted",
      header: t("columns.granted"),
      cellClassName: "text-muted-foreground text-xs",
      cell: (rb) => new Date(rb.grantedAt).toLocaleDateString(),
    },
    {
      id: "expires",
      header: t("columns.expires"),
      cellClassName: "text-muted-foreground text-xs",
      cell: (rb) => (rb.expiresAt ? new Date(rb.expiresAt).toLocaleDateString() : t("never")),
    },
    {
      id: "actions",
      header: "",
      align: "right",
      width: "w-16",
      cell: (rb) => (
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
      ),
    },
  ];

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
      description={
        <span className="text-muted-foreground font-mono text-xs">
          {t("description", { slug: a.slug })}
        </span>
      }
    >
      <AppTabs slug={a.slug} active="members" />

      <Card>
        <CardContent className="p-4">
          <p className="text-muted-foreground text-xs uppercase tracking-wide">
            {t("availableRoles")}
          </p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {appRoles.map((r) => (
              <Badge key={r.id} variant="outline" className="font-mono text-2xs">
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

      <DataTable
        label="App members"
        controller={table}
        columns={columns}
        getRowId={(rb) => rb.id}
        searchPlaceholder={t("searchPlaceholder")}
        empty={{
          icon: <ShieldIcon className="size-5" />,
          title: t("emptyTitle"),
          description: t("emptyDescription"),
          actionHref: "/administration/members",
          actionLabel: t("openMembers"),
        }}
        emptyFiltered={{
          title: "No matching members",
          description:
            "No grant on this app matches that search. The server matches the user, the group, and the role.",
        }}
      />

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
