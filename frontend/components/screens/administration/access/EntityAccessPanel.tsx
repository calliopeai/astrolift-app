"use client";

import { ShieldIcon, Trash2Icon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { type RoleRef, SCOPE_NOUN } from "@/components/access/access-model";
import { GrantSource } from "@/components/access/GrantSource";
import { PrincipalChip } from "@/components/access/PrincipalChip";
import { RoleSummary } from "@/components/access/RoleSummary";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import type { ListStateController } from "@/components/list/list-state";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { useFormatters } from "@/lib/i18n/formatters";

import {
  type AccessEntry,
  entryKey,
  principalOfEntry,
  removalOf,
  scopeOfEntry,
  SOURCE_LABEL,
  sourceOfEntry,
} from "./entity-access";

export interface EntityAccessPanelProps {
  list: ListStateController;
  /** The object, as the confirm and the empty state name it: "team platform". */
  subject: string;
  /** One page of `astroliftAccessOn`. */
  rows: AccessEntry[];
  totalCount: number;
  loading: boolean;
  stale?: boolean;
  error: { message: string } | null;
  onRetry: () => void;
  canManage: boolean;
  /** Throws on failure, so the confirm stays open and says why. */
  onRemove: (entry: AccessEntry) => Promise<void>;
  /** The Grant access link for the empty state, preselected to this object. */
  grantHref?: string;
}

/**
 * Who has access on one object, and why (access UX design 3.3): every
 * person, IdP group and team with a grant here, one row per grant, from the
 * server's own answer (`astroliftAccessOn`). Each row names the role, the
 * source (a grant, a group's grant or mapping, a team share) and, when it is
 * held higher up, where. Remove takes a grant or mapping away at its
 * source, after a confirm that says so; a team share is changed on the
 * app's own sharing. Pure.
 */
export function EntityAccessPanel({
  list,
  subject,
  rows,
  totalCount,
  loading,
  stale,
  error,
  onRetry,
  canManage,
  onRemove,
  grantHref,
}: EntityAccessPanelProps) {
  const t = useTranslations("shared.access.entityPanel"),
    accessT = useTranslations("shared.access");
  const principal = (entry: AccessEntry) =>
    principalOfEntry(entry, (count) => (count == null ? t("group") : t("groupCount", { count })));
  const kind = (value: string) =>
    Object.hasOwn(SCOPE_NOUN, value) ? accessT(`scope.${value}`) : value;
  const fmt = useFormatters();
  const [target, setTarget] = React.useState<AccessEntry | null>(null);

  const columns: Column<AccessEntry>[] = [
    {
      id: "principal",
      header: t("who"),
      cellClassName: "relative z-10 max-w-64",
      cell: (e) => <PrincipalChip principal={principal(e)} variant="block" />,
    },
    {
      id: "role",
      header: t("role"),
      cellClassName: "max-w-80",
      cell: (e) =>
        e.role ? (
          <RoleSummary
            role={{ ...e.role, scopeLevel: e.role.scopeLevel as RoleRef["scopeLevel"] }}
            expandable={false}
          />
        ) : (
          <span className="text-muted-foreground font-mono text-xs">
            {t("share", { level: e.accessLevel ?? "—" })}
          </span>
        ),
    },
    {
      id: "source",
      header: t("source"),
      cellClassName: "relative z-10 max-w-64",
      cell: (e) => (
        <div className="flex min-w-0 flex-col items-start gap-1">
          <GrantSource source={sourceOfEntry(e)} />
          <span className="text-muted-foreground text-2xs">
            {Object.hasOwn(SOURCE_LABEL, e.source) ? t(`sourceLabels.${e.source}`) : e.source}
          </span>
        </div>
      ),
    },
    {
      id: "expires",
      header: t("expires"),
      cellClassName: "text-muted-foreground font-mono text-xs",
      cell: (e) => (e.expiresAt ? fmt.formatDate(e.expiresAt) : t("never")),
    },
  ];

  const where = target ? scopeOfEntry(target) : null;

  return (
    <div className="flex min-w-0 flex-col gap-3">
      <ListPage<AccessEntry>
        embedded
        list={list}
        label={t("label")}
        columns={columns}
        rows={rows}
        getRowId={entryKey}
        loading={loading}
        stale={stale}
        error={error}
        onRetry={onRetry}
        totalCount={totalCount}
        rowActions={
          canManage
            ? (e) =>
                removalOf(e) ? (
                  <DropdownMenuItem variant="destructive" onSelect={() => setTarget(e)}>
                    <Trash2Icon className="size-4" />
                    {removalOf(e) === "mapping" ? t("removeMapping") : t("removeGrant")}
                  </DropdownMenuItem>
                ) : (
                  <DropdownMenuItem disabled>
                    {e.source === "TEAM_SHARE" ? t("appSharing") : t("notRemovable")}
                  </DropdownMenuItem>
                )
            : undefined
        }
        empty={{
          icon: <ShieldIcon className="size-5" />,
          title: t("emptyTitle"),
          description: t("emptyDescription", { subject }),
          ...(grantHref && canManage ? { actionHref: grantHref, actionLabel: t("grant") } : {}),
        }}
      />
      <ConfirmDialog
        open={target !== null}
        onOpenChange={(next) => {
          if (!next) setTarget(null);
        }}
        title={
          target && where
            ? t("removeTitle", {
                role: target.role?.slug ?? t("accessName"),
                name: principal(target).name,
              })
            : t("removeGeneric")
        }
        description={
          target && where
            ? [
                target.inherited
                  ? t("inherited", { kind: kind(where.kind), name: where.name, subject })
                  : t("direct", { subject }),
                removalOf(target) === "mapping" ? t("groupLoss") : "",
                t("otherGrants"),
              ]
                .filter(Boolean)
                .join(" ")
            : ""
        }
        confirmLabel={t("remove")}
        confirmDisabled={
          !canManage || !target || !rows.some((row) => entryKey(row) === entryKey(target))
        }
        destructive
        onConfirm={async () => {
          if (target) await onRemove(target);
          setTarget(null);
        }}
      />
    </div>
  );
}
