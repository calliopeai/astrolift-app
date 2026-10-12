"use client";

import { LockIcon, PlusIcon, ShieldIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { summarizePermissions } from "@/components/access/access-model";
import { Can } from "@/components/Can";
import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { AstroliftRole } from "@/graphql/identity/identity.types";

import { permissionsCrumbs } from "../access/admin-crumbs";
import { newRoleHref, roleHref } from "./roles-routes";
import { SCOPE_TONE } from "./scope-tone";
import type { useRoles } from "./use-roles";

export type RolesViewProps = ReturnType<typeof useRoles>;

type Translator = (key: string, values?: Record<string, string>) => string;

const columns = (t: Translator, scopes: Translator): Column<AstroliftRole>[] => [
  {
    id: "role",
    header: t("columns.role"),
    sortKey: "name",
    cellClassName: "max-w-72",
    cell: (role) => (
      <span className="block min-w-0">
        <span className="block truncate font-medium" title={role.name}>
          {role.name}
        </span>
        <span className="text-muted-foreground block truncate font-mono text-xs" title={role.slug}>
          {role.slug}
        </span>
      </span>
    ),
  },
  {
    id: "allows",
    header: t("columns.allows"),
    cellClassName: "max-w-96",
    cell: (role) => {
      const text = role.description?.trim() || summarizePermissions(role.permissions);
      return (
        <span className="text-muted-foreground line-clamp-2 min-w-0 text-sm" title={text}>
          {text}
        </span>
      );
    },
  },
  {
    id: "scope",
    header: t("columns.bindsAt"),
    sortKey: "scopeLevel",
    cell: (role) => (
      <Badge className={`${SCOPE_TONE[role.scopeLevel] ?? ""} font-mono`} variant="secondary">
        {scopes(role.scopeLevel)}
      </Badge>
    ),
  },
  {
    id: "type",
    header: t("columns.kind"),
    cell: (role) =>
      role.isSystem ? (
        <Badge variant="outline" className="gap-1">
          <LockIcon className="size-3" />
          {t("builtIn")}
        </Badge>
      ) : (
        <span className="flex min-w-0 flex-col items-start gap-0.5">
          <Badge variant="secondary">{t("custom")}</Badge>
          {role.duplicatedFrom && (
            <span
              className="text-muted-foreground block max-w-40 truncate text-xs"
              title={t("duplicatedFrom", { name: role.duplicatedFrom.name })}
            >
              {role.duplicatedFrom.deleted
                ? t("fromDeleted", { name: role.duplicatedFrom.name })
                : t("from", { name: role.duplicatedFrom.name })}
            </span>
          )}
        </span>
      ),
  },
  {
    id: "permissions",
    header: t("columns.permissions"),
    align: "right",
    cellClassName: "font-mono tabular-nums",
    cell: (role) => role.permissions.length,
  },
  {
    // Bindings in this org; a shared built-in counts only this org's.
    id: "holders",
    header: t("columns.holders"),
    sortKey: "bindings",
    align: "right",
    cellClassName: "font-mono tabular-nums",
    cell: (role) => role.bindingsCount ?? "—",
  },
];

/**
 * Admin › Permissions › Roles: the roles list (design 3.5, spec 44 §5.1).
 * Each row opens the role's page, where its permissions read as a matrix,
 * its holders are listed and a custom role is edited. New role is a page
 * with steps (five fields, spec 44 §5.4), not a sheet.
 */
export function RolesView({ list, page }: RolesViewProps) {
  const t = useTranslations("adminRoles");
  const scopes = useTranslations("shared.access.scope");
  return (
    <div className="flex min-w-0 flex-1 flex-col p-6">
      <ListPage<AstroliftRole>
        header={{
          crumbs: permissionsCrumbs("roles"),
          title: t("title"),
          context: t("context"),
          primaryAction: (
            <Can permission="org.manage_members">
              <Button size="sm" asChild>
                <Link href={newRoleHref()}>
                  <PlusIcon className="size-4" />
                  {t("newRole")}
                </Link>
              </Button>
            </Can>
          ),
        }}
        list={list}
        label={t("title")}
        // A scope level the catalog does not name reads as itself.
        columns={columns(t, (key) => (scopes.has(key) ? scopes(key) : key))}
        rows={page.rows}
        getRowId={(role) => role.id}
        rowHref={(role) => roleHref(role.id)}
        loading={page.loading}
        stale={page.stale}
        error={page.error}
        onRetry={page.refetch}
        totalCount={page.totalCount}
        nextCursor={page.nextCursor}
        empty={{
          icon: <ShieldIcon className="size-5" />,
          title: t("emptyTitle"),
          description: t("emptyDescription"),
        }}
      />
    </div>
  );
}
