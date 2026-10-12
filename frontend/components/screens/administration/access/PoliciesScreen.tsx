"use client";

import { InfoIcon, PlusIcon, ScaleIcon, Trash2Icon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { parsePolicy } from "@/components/access/policy-model";
import { PolicySentence } from "@/components/access/PolicySentence";
import type { AstroliftPolicy } from "@/graphql/identity/identity.types";
import { DOC_LINKS } from "@/lib/docs/urls";
import { useFormatters } from "@/lib/i18n/formatters";

import { NEW_POLICY_HREF, policiesCrumbs, policyHref } from "./policy-routes";
import type { usePolicies } from "./use-policies";

export type PoliciesScreenProps = ReturnType<typeof usePolicies>;

/**
 * Admin › Policies: the ABAC policy list (design 3.6, spec 44 §5.1), each row
 * read as its sentence and opening the policy's page, with soft-delete. The
 * permission check evaluates every policy (#2157): a DENY that matches wins
 * over any role, and a condition the request cannot answer denies.
 */
export function PoliciesScreen({
  list,
  page,
  canManage,
  deleting,
  deletePolicy,
}: PoliciesScreenProps) {
  const fmt = useFormatters();
  const t = useTranslations("adminPolicies");
  const scopes = useTranslations("shared.access.scope");
  const [deleteTarget, setDeleteTarget] = React.useState<AstroliftPolicy | null>(null);

  const columns: Column<AstroliftPolicy>[] = [
    {
      id: "policy",
      header: t("columns.policy"),
      sortKey: "name",
      cellClassName: "max-w-[36rem]",
      // The sentence is the row (design 3.6): what it denies, on what, for
      // whom and unless what, read the way the editor builds it.
      cell: (p) => (
        <span className="flex min-w-0 flex-col gap-0.5">
          <span className="flex min-w-0 items-baseline gap-2">
            <span className="truncate font-medium" title={p.name}>
              {p.name}
            </span>
            <span
              className="text-muted-foreground min-w-0 truncate font-mono text-xs"
              title={p.slug}
            >
              {p.slug}
            </span>
          </span>
          <PolicySentence policy={parsePolicy(p)} className="text-muted-foreground line-clamp-2" />
        </span>
      ),
    },
    {
      id: "effect",
      header: t("columns.effect"),
      sortKey: "effect",
      cell: (p) => (
        <Badge variant={p.effect === "DENY" ? "destructive" : "secondary"} className="font-mono">
          {p.effect.toLowerCase()}
        </Badge>
      ),
    },
    {
      id: "scope",
      header: t("columns.scope"),
      sortKey: "scopeLevel",
      cell: (p) => (
        <Badge variant="outline" className="font-mono">
          {scopes.has(p.scopeLevel) ? scopes(p.scopeLevel) : p.scopeLevel}
        </Badge>
      ),
    },
    {
      id: "createdBy",
      header: t("columns.createdBy"),
      cellClassName: "max-w-48",
      cell: (p) => (
        <span
          className="text-muted-foreground block min-w-0 truncate font-mono text-xs"
          title={p.createdByUsername ?? undefined}
        >
          {p.createdByUsername ?? t("unknown")}
        </span>
      ),
    },
    {
      id: "updatedAt",
      header: t("columns.updated"),
      sortKey: "updated",
      cellClassName: "text-muted-foreground font-mono text-xs",
      cell: (p) => fmt.formatDate(p.updatedAt || p.createdAt),
    },
  ];

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6 p-6">
      <ListPage<AstroliftPolicy>
        header={{
          crumbs: policiesCrumbs(),
          title: t("title"),
          context: t("context"),
          primaryAction: (
            <Can permission="org.update">
              <Button size="sm" asChild>
                <Link href={NEW_POLICY_HREF}>
                  <PlusIcon className="size-4" />
                  {t("newPolicy")}
                </Link>
              </Button>
            </Can>
          ),
        }}
        list={list}
        label={t("title")}
        columns={columns}
        rows={page.rows}
        getRowId={(p) => p.id}
        rowHref={(p) => policyHref(p.id)}
        loading={page.loading}
        stale={page.stale}
        error={page.error}
        onRetry={page.refetch}
        totalCount={page.totalCount}
        // DENY rows get a flush left border in destructive tone so the
        // danger-zone read is obvious at a glance: a DENY can lock operators
        // out even when role bindings would otherwise permit the call.
        rowClassName={(p) =>
          p.effect === "DENY" ? "border-l-destructive/70 border-l-2" : undefined
        }
        rowActions={
          canManage
            ? (p) => (
                <DropdownMenuItem
                  variant="destructive"
                  disabled={deleting}
                  onSelect={() => setDeleteTarget(p)}
                >
                  <Trash2Icon className="size-4" />
                  {t("delete")}
                </DropdownMenuItem>
              )
            : undefined
        }
        empty={{
          icon: <ScaleIcon className="size-5" />,
          title: t("emptyTitle"),
          description: t("emptyDescription"),
          actionHref: canManage ? NEW_POLICY_HREF : undefined,
          actionLabel: canManage ? t("newPolicy") : undefined,
          learnMoreHref: DOC_LINKS.policies,
        }}
      />

      <div className="border-warning-border bg-warning/10 flex min-w-0 items-start gap-2 rounded-md border p-3">
        <InfoIcon className="text-warning-fg mt-0.5 size-4 shrink-0" />
        <p className="text-muted-foreground min-w-0 text-xs leading-relaxed">
          {t.rich("enforced", {
            strong: (chunks) => <span className="text-foreground font-medium">{chunks}</span>,
          })}{" "}
          <Link className="underline underline-offset-2" href={DOC_LINKS.policies}>
            {t("learnMore")}
          </Link>
        </p>
      </div>

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(next) => {
          if (!next) setDeleteTarget(null);
        }}
        title={deleteTarget ? t("deleteTitle", { slug: deleteTarget.slug }) : t("deleteFallback")}
        description={t("deleteDescription")}
        confirmLabel={t("deleteConfirm")}
        destructive
        onConfirm={async () => {
          if (deleteTarget) await deletePolicy(deleteTarget);
        }}
      />
    </div>
  );
}
