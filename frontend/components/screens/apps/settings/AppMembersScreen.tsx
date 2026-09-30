"use client";

import { SearchCheckIcon, ShieldIcon, UserPlusIcon, UsersIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";

import { GrantSource } from "@/components/access/GrantSource";
import { PrincipalChip } from "@/components/access/PrincipalChip";
import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { Column } from "@/components/data-table";
import { EmptyState } from "@/components/EmptyState";
import { ListPage } from "@/components/list/ListPage";
import type { ListStateController } from "@/components/list/list-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { useFormatters } from "@/lib/i18n/formatters";

import type { AccessRow } from "./app-access-rows";

export interface AppMembersScreenProps {
  /** The app (or agent) being looked at; null when it does not exist or is not visible. */
  app: { id: string; slug: string } | null;
  /** The app itself, first load. */
  loading: boolean;
  list: ListStateController;
  rows: AccessRow[];
  rowsLoading: boolean;
  stale?: boolean;
  error: { message: string } | null;
  onRetry: () => void;
  totalCount?: number | null;
  nextCursor?: string | null;
  removing: boolean;
  /** Removes at the source; throws so the confirm dialog shows why. */
  onRemove: (row: AccessRow) => Promise<void>;
  /** The Grant access flow with this app preselected. */
  grantHref: string;
  /** Check access for the row's person on this app. */
  checkHref: (row: AccessRow) => string;
  slug: string;
}

/**
 * People with access (design 3.3), the Access tab's first section on an app
 * or an agent: who can get in and why. One embedded list of principals (a
 * user, an IdP group, a team) with the role or access level they hold and
 * the grant's source, role bindings and team shares together. Grant access
 * opens the one flow with this app preselected; Remove acts at the source;
 * each person links to Check access on this app. Pure.
 */
export function AppMembersScreen({
  app: a,
  loading,
  list,
  rows,
  rowsLoading,
  stale,
  error,
  onRetry,
  totalCount,
  nextCursor,
  removing,
  onRemove,
  grantHref,
  checkHref,
  slug,
}: AppMembersScreenProps) {
  const fmt = useFormatters();
  const t = useTranslations("apps.members");
  const common = useTranslations("apps.common");
  const [target, setTarget] = React.useState<AccessRow | null>(null);

  if (loading) {
    return (
      <div className="flex min-w-0 flex-col gap-3" aria-busy>
        <Skeleton className="h-9 w-40" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }

  if (!a) {
    return (
      <EmptyState
        icon={<UsersIcon className="size-5" />}
        title={common("notFoundSlug", { slug })}
        description={common("notFoundDescription")}
        actionHref="/apps"
        actionLabel={common("backToApps")}
      />
    );
  }

  const columns: Column<AccessRow>[] = [
    {
      id: "principal",
      header: t("columns.who"),
      cell: (r) => (
        <PrincipalChip
          principal={
            r.kind === "team_share" && r.share.isHome
              ? { ...r.principal, detail: t("homeTeam") }
              : r.principal
          }
          variant="block"
        />
      ),
    },
    {
      id: "role",
      header: t("columns.role"),
      cell: (r) => (
        <span className="flex min-w-0 flex-col">
          <span className="min-w-0 truncate text-sm" title={r.role.name}>
            {r.role.name}
          </span>
          {r.role.slug !== r.role.name && (
            <span className="text-muted-foreground text-2xs min-w-0 truncate font-mono">
              {r.role.slug}
            </span>
          )}
        </span>
      ),
    },
    {
      id: "source",
      header: t("columns.source"),
      cell: (r) =>
        r.kind === "team_share" ? (
          <Badge variant="outline" className="text-2xs">
            {t("teamShare")}
          </Badge>
        ) : (
          <GrantSource source={r.source} />
        ),
    },
    {
      id: "expires",
      header: t("columns.expires"),
      cellClassName: "text-muted-foreground font-mono text-xs whitespace-nowrap",
      cell: (r) => (r.expiresAt ? fmt.formatDateTime(r.expiresAt) : t("never")),
    },
    {
      id: "granted",
      header: t("columns.granted"),
      cellClassName: "text-muted-foreground font-mono text-xs whitespace-nowrap",
      cell: (r) => fmt.formatDateTime(r.grantedAt),
    },
  ];

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
        <p className="text-muted-foreground max-w-2xl min-w-0 text-sm">
          {t.rich("summary", { app: () => <span className="font-mono">{a.slug}</span> })}
        </p>
        <Can permission="org.manage_members">
          <Button asChild size="sm">
            <Link href={grantHref}>
              <UserPlusIcon className="size-4" /> {t("grantAccess")}
            </Link>
          </Button>
        </Can>
      </div>

      <ListPage<AccessRow>
        embedded
        list={list}
        label={t("peopleTitle")}
        columns={columns}
        rows={rows}
        getRowId={(r) => r.id}
        rowActions={(r) => (
          <>
            {r.principal.kind === "user" && (
              <DropdownMenuItem asChild>
                <Link href={checkHref(r)}>
                  <SearchCheckIcon className="size-4" /> {t("checkAccess")}
                </Link>
              </DropdownMenuItem>
            )}
            {r.principal.href && (
              <DropdownMenuItem asChild>
                <Link href={r.principal.href}>
                  <UsersIcon className="size-4" /> {t(`openPrincipal.${r.principal.kind}`)}
                </Link>
              </DropdownMenuItem>
            )}
            <Can permission={r.kind === "binding" ? "org.manage_members" : "app.update"}>
              <DropdownMenuItem
                variant="destructive"
                disabled={removing || (r.kind === "team_share" && r.share.isHome)}
                onSelect={() => setTarget(r)}
              >
                {r.kind === "binding" ? t("removeRole") : t("endTeamShare")}
              </DropdownMenuItem>
            </Can>
          </>
        )}
        loading={rowsLoading}
        stale={stale}
        error={error}
        onRetry={onRetry}
        empty={{
          icon: <ShieldIcon className="size-5" />,
          title: t("emptyCurrentTitle"),
          description: t("emptyCurrentDescription"),
          actionHref: grantHref,
          actionLabel: t("grantAccess"),
        }}
        totalCount={totalCount}
        nextCursor={nextCursor}
      />

      <ConfirmDialog
        open={target !== null}
        onOpenChange={(next) => {
          if (!next) setTarget(null);
        }}
        title={
          target
            ? target.kind === "binding"
              ? t("removeConfirm.bindingTitle", {
                  role: target.role.slug,
                  target: target.principal.name,
                })
              : t("removeConfirm.shareTitle", { target: target.principal.name, app: a.slug })
            : t("removeConfirm.fallbackTitle")
        }
        description={
          target?.kind === "team_share"
            ? t("removeConfirm.shareDescription")
            : t("removeConfirm.bindingDescription")
        }
        confirmLabel={target?.kind === "team_share" ? t("endShare") : t("removeRole")}
        destructive
        onConfirm={async () => {
          if (target) await onRemove(target);
        }}
      />
    </div>
  );
}
