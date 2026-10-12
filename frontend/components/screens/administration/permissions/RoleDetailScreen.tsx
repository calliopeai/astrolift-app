"use client";

import { LockIcon, SearchXIcon, UserPlusIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import type * as React from "react";

import { summarizePermissions } from "@/components/access/access-model";
import { EmptyState } from "@/components/EmptyState";
import { Panel } from "@/components/panel/Panel";
import { ShellHeader } from "@/components/shell/ShellHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

import { RolePermissionsTab } from "./RolePermissionsTab";
import { RoleSettingsTab } from "./RoleSettingsTab";
import {
  grantRoleHref,
  newRoleHref,
  ROLES_HREF,
  roleCrumbs,
  type RoleTab,
  roleTabs,
} from "./roles-routes";
import type { useRoleDetail } from "./use-role-detail";

export type RoleDetailScreenProps = ReturnType<typeof useRoleDetail> & {
  tab: RoleTab;
  /**
   * The Holders tab's list, rendered by the route only while that tab is
   * open, so its bindings query runs only then (Leo's page rule 2).
   */
  holders?: React.ReactNode;
};

/**
 * A role's page (design 3.5, spec 44 §5.2): the role in one line, then one
 * row of tabs, each its own route: Permissions (the matrix), Holders (who
 * holds it and where) and Settings. The primary action is Grant access;
 * built-in roles offer Duplicate to customise beside their matrix, custom
 * roles edit in place. Only the open tab is mounted. Pure.
 */
export function RoleDetailScreen({
  id,
  role,
  roles,
  catalog,
  loading,
  error,
  onRetry,
  canManage,
  saving,
  savePermissions,
  saveSettings,
  tab,
  holders,
}: RoleDetailScreenProps) {
  const t = useTranslations("adminRoleDetail");
  const scopes = useTranslations("shared.access.scope");
  if (!role) {
    return (
      <div className="flex min-w-0 flex-1 flex-col gap-6 p-6">
        <ShellHeader
          crumbs={roleCrumbs(loading ? t("role") : t("notFoundCrumb"))}
          title={loading ? <Skeleton className="h-6 w-48" /> : t("notFoundTitle")}
        />
        {loading ? (
          <Panel title={t("permissions")} loading />
        ) : error ? (
          <Panel title={t("role")} error={error} onRetry={onRetry} />
        ) : (
          <EmptyState
            icon={<SearchXIcon className="size-5" />}
            title={t("missingTitle")}
            description={t("missingDescription", { id })}
            actionHref={ROLES_HREF}
            actionLabel={t("allRoles")}
          />
        )}
      </div>
    );
  }

  // One primary action on every role; a built-in's Duplicate sits with the
  // matrix it would copy.
  const primaryAction = canManage ? (
    <Button size="sm" asChild>
      <Link href={grantRoleHref(role.id)}>
        <UserPlusIcon className="size-4" />
        {t("grantAccess")}
      </Link>
    </Button>
  ) : undefined;

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6 p-6">
      <ShellHeader
        crumbs={roleCrumbs(role.name)}
        title={role.name}
        status={
          <span className="flex min-w-0 items-center gap-1.5">
            <Badge variant="outline" className="font-mono uppercase">
              {scopes.has(role.scopeLevel) ? scopes(role.scopeLevel) : role.scopeLevel}
            </Badge>
            {role.isSystem ? (
              <Badge variant="secondary" className="gap-1">
                <LockIcon aria-hidden className="size-3" />
                {t("builtIn")}
              </Badge>
            ) : (
              <Badge variant="secondary">{t("custom")}</Badge>
            )}
          </span>
        }
        context={
          <span title={role.description || undefined}>
            {role.description?.trim() || summarizePermissions(role.permissions, catalog)}
          </span>
        }
        primaryAction={primaryAction}
        tabs={roleTabs(role.id, tab, (key) => t(`tabs.${key}`))}
        tabsAriaLabel={t("role")}
      />

      {tab === "permissions" && (
        <RolePermissionsTab
          key={role.permissions.join(" ")}
          role={role}
          roles={roles}
          catalog={catalog}
          canManage={canManage}
          saving={saving}
          onSave={savePermissions}
          duplicateHref={newRoleHref(role.id)}
        />
      )}
      {tab === "holders" && holders}
      {tab === "settings" && (
        <RoleSettingsTab role={role} canManage={canManage} saving={saving} onSave={saveSettings} />
      )}
    </div>
  );
}
