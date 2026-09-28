"use client";

import { ShieldIcon, Trash2Icon, UsersIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DataTable, type Column } from "@/components/data-table";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { AstroliftRoleBinding } from "@/graphql/identity/identity.types";

import type { useAppMembers } from "./use-app-members";

export type AppMembersScreenProps = ReturnType<typeof useAppMembers> & {
  slug: string;
  /** The app detail tab row. */
  tabs?: React.ReactNode;
};

/**
 * The app members tab: the roles available at APP scope and every role
 * binding on this app, each revocable by an org member manager.
 */
export function AppMembersScreen({
  app: a,
  loading,
  appRoles,
  table,
  revoking,
  onRevoke,
  slug,
  tabs,
}: AppMembersScreenProps) {
  const tCommon = useTranslations("apps.common");
  const t = useTranslations("apps.members");
  const [revokeTarget, setRevokeTarget] = React.useState<AstroliftRoleBinding | null>(null);

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
        <Badge variant="secondary" className="text-2xs font-mono">
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

  if (loading) {
    return (
      <PageShell title={t("loadingTitle")} description={tCommon("loading")}>
        <Skeleton className="h-32 w-full" />
      </PageShell>
    );
  }

  if (!a) {
    return (
      <PageShell title={tCommon("notFound")} description={tCommon("notFoundPermission")}>
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
      {tabs}

      <Card>
        <CardContent className="p-4">
          <p className="text-muted-foreground text-xs tracking-wide uppercase">
            {t("availableRoles")}
          </p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {appRoles.map((r) => (
              <Badge key={r.id} variant="outline" className="text-2xs font-mono">
                {r.slug}
              </Badge>
            ))}
            {appRoles.length === 0 && (
              <span className="text-muted-foreground text-xs">{t("noRolesDefined")}</span>
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
          if (revokeTarget) await onRevoke(revokeTarget);
        }}
      />
    </PageShell>
  );
}
