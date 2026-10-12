"use client";

import { AlertTriangleIcon, ShieldIcon, Trash2Icon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";

import { principalOfBinding } from "@/components/access/access-model";
import { GrantSource } from "@/components/access/GrantSource";
import { PrincipalChip } from "@/components/access/PrincipalChip";
import { RoleSummary } from "@/components/access/RoleSummary";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { Column, RowSelection } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import type { ListStateController } from "@/components/list/list-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import type { AstroliftRoleBinding } from "@/graphql/identity/identity.types";
import { useFormatters } from "@/lib/i18n/formatters";

import type { AccessRow } from "./principal-access";

export interface PrincipalAccessPanelProps {
  list: ListStateController;
  /** The page on screen. */
  rows: AccessRow[];
  totalCount: number;
  /** What they hold in one line, over every grant (see summarizeAccess). */
  summary: string | null;
  loading: boolean;
  stale?: boolean;
  error: { message: string } | null;
  truncated?: boolean;
  onRetry: () => void;
  canManage: boolean;
  revoking?: boolean;
  onRevoke: (binding: AstroliftRoleBinding) => Promise<void>;
  onBulkRevoke: (ids: string[]) => Promise<boolean>;
  /** A team's page lists every holder, so it adds a Who column. */
  showHolder?: boolean;
  /** A caveat above the list. */
  note?: React.ReactNode;
  /** The Grant access link for the empty state. */
  grantHref?: string;
  /** Who loses access, for the remove confirm: "ada", "okta:eng", "everyone on payments". */
  holderLabel: (binding: AstroliftRoleBinding) => string;
}

type RemoveTarget = { one: AccessRow } | { many: RowSelection };

/**
 * A principal's (or a team's) Access tab (access UX design 3.2): effective
 * access by scope, widest first, one row per grant. Each row names its scope
 * and role, says where the grant comes from (`GrantSource`) and whether a
 * wider grant of the same role also gives it, which is what someone needs to
 * see before removing either. Remove revokes the grant at its source, after
 * a confirm that says who loses what. `can:app.deploy` narrows to the grants
 * that carry a permission. Pure: data from usePrincipalAccess.
 */
export function PrincipalAccessPanel({
  list,
  rows,
  totalCount,
  summary,
  loading,
  stale,
  error,
  truncated,
  onRetry,
  canManage,
  revoking,
  onRevoke,
  onBulkRevoke,
  showHolder = false,
  note,
  grantHref,
  holderLabel,
}: PrincipalAccessPanelProps) {
  const fmt = useFormatters();
  const t = useTranslations("adminPrincipalAccess");
  const scopes = useTranslations("shared.access.scope");
  const [target, setTarget] = React.useState<RemoveTarget | null>(null);

  const columns: Column<AccessRow>[] = [
    {
      id: "scope",
      header: t("columns.scope"),
      sortKey: "scope",
      cellClassName: "max-w-64",
      cell: (row) => (
        <div className="flex min-w-0 items-center gap-1.5">
          <Badge variant="outline" className="text-2xs shrink-0 font-mono uppercase">
            {scopes(row.scope.kind)}
          </Badge>
          {row.scope.href ? (
            <Link
              href={row.scope.href}
              className="relative z-10 min-w-0 truncate font-mono text-xs hover:underline"
              title={row.scope.name}
            >
              {row.scope.name}
            </Link>
          ) : (
            <span className="min-w-0 truncate font-mono text-xs" title={row.scope.name}>
              {row.scope.name}
            </span>
          )}
        </div>
      ),
    },
    ...(showHolder
      ? [
          {
            id: "holder",
            header: t("columns.who"),
            sortKey: "principal",
            cellClassName: "max-w-56",
            cell: (row: AccessRow) => <PrincipalChip principal={principalOfBinding(row.binding)} />,
          } satisfies Column<AccessRow>,
        ]
      : []),
    {
      id: "role",
      header: t("columns.role"),
      sortKey: "role",
      cellClassName: "max-w-80",
      cell: (row) =>
        row.role ? (
          <RoleSummary role={row.role} expandable={false} />
        ) : (
          <div className="min-w-0">
            <div className="truncate font-medium">{row.binding.role.name}</div>
            <div className="text-muted-foreground truncate font-mono text-xs">
              {row.binding.role.slug}
            </div>
          </div>
        ),
    },
    {
      id: "source",
      header: t("columns.source"),
      cellClassName: "relative z-10 max-w-64",
      cell: (row) => (
        <div className="flex min-w-0 flex-col items-start gap-1">
          <GrantSource source={row.source} />
          {row.coveredBy && (
            <span className="text-muted-foreground text-2xs [overflow-wrap:anywhere]">
              {t.rich("alsoGranted", {
                kind: scopes(row.coveredBy.kind),
                name: row.coveredBy.name,
                mono: (chunks) => <span className="font-mono">{chunks}</span>,
              })}
            </span>
          )}
        </div>
      ),
    },
    {
      id: "expires",
      header: t("columns.expires"),
      sortKey: "expires",
      cellClassName: "text-muted-foreground font-mono text-xs",
      cell: (row) => (row.binding.expiresAt ? fmt.formatDate(row.binding.expiresAt) : t("never")),
    },
    {
      id: "granted",
      header: t("columns.granted"),
      sortKey: "granted",
      cellClassName: "text-muted-foreground font-mono text-xs",
      cell: (row) => fmt.formatDate(row.binding.grantedAt),
    },
  ];

  const confirm = target ? describeRemove(target, holderLabel, t, (kind) => scopes(kind)) : null;

  return (
    <div className="flex min-w-0 flex-col gap-3">
      {(summary || note || truncated) && (
        <div className="flex min-w-0 flex-col gap-2">
          {summary && (
            <p className="text-muted-foreground text-xs [overflow-wrap:anywhere]">{summary}</p>
          )}
          {truncated && <Note>{t("truncated", { count: 200 })}</Note>}
          {note && <Note>{note}</Note>}
        </div>
      )}
      <ListPage<AccessRow>
        embedded
        list={list}
        label={t("listLabel")}
        columns={columns}
        rows={rows}
        getRowId={(r) => r.id}
        loading={loading}
        stale={stale}
        error={error}
        onRetry={onRetry}
        totalCount={totalCount}
        rowActions={
          canManage
            ? (row) => (
                <DropdownMenuItem
                  variant="destructive"
                  disabled={revoking}
                  onSelect={() => setTarget({ one: row })}
                >
                  <Trash2Icon className="size-4" />
                  {t("removeThis")}
                </DropdownMenuItem>
              )
            : undefined
        }
        bulkActions={
          canManage
            ? (selection) => (
                <Button
                  size="sm"
                  variant="destructive"
                  disabled={revoking}
                  onClick={() => setTarget({ many: selection })}
                >
                  <Trash2Icon className="size-4" />
                  {t("removeCount", { count: selection.selectedCount })}
                </Button>
              )
            : undefined
        }
        empty={{
          icon: <ShieldIcon className="size-5" />,
          title: t("emptyTitle"),
          description: t("emptyDescription"),
          ...(grantHref && canManage
            ? { actionHref: grantHref, actionLabel: t("grantAccess") }
            : {}),
        }}
      />
      <ConfirmDialog
        open={confirm !== null}
        onOpenChange={(next) => {
          if (!next) setTarget(null);
        }}
        title={confirm?.title ?? ""}
        description={confirm?.description ?? ""}
        confirmLabel={confirm?.confirmLabel ?? t("remove")}
        destructive
        onConfirm={async () => {
          if (!target) return;
          if ("one" in target) {
            await onRevoke(target.one.binding);
          } else if (await onBulkRevoke(target.many.selectedIds)) {
            target.many.clear();
          }
          setTarget(null);
        }}
      />
    </div>
  );
}

function describeRemove(
  target: RemoveTarget,
  holderLabel: (b: AstroliftRoleBinding) => string,
  t: (key: string, values?: Record<string, string | number>) => string,
  scopeNoun: (kind: string) => string
): { title: string; description: string; confirmLabel: string } {
  if ("many" in target) {
    const count = target.many.selectedCount;
    return {
      title: t("removeManyTitle", { count }),
      description: t("removeManyDescription"),
      confirmLabel: t("removeCount", { count }),
    };
  }
  const { binding, scope, coveredBy } = target.one;
  const where = { kind: scopeNoun(scope.kind), name: scope.name };
  return {
    title: t("removeOneTitle", { role: binding.role.slug, ...where }),
    description: coveredBy
      ? t("removeCovered", {
          holder: holderLabel(binding),
          kind: scopeNoun(coveredBy.kind),
          name: coveredBy.name,
        })
      : t("removeOneDescription", {
          holder: holderLabel(binding),
          role: binding.role.name,
          ...where,
        }),
    confirmLabel: t("removeGrant"),
  };
}

function Note({ children }: { children: React.ReactNode }) {
  return (
    <div className="border-warning-border bg-warning/10 text-warning-fg flex min-w-0 items-start gap-2 rounded-md border px-3 py-2 text-xs">
      <AlertTriangleIcon aria-hidden className="mt-0.5 size-3.5 shrink-0" />
      <div className="min-w-0 [overflow-wrap:anywhere]">{children}</div>
    </div>
  );
}
