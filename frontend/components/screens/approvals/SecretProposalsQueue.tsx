"use client";

import { KeyIcon, RefreshCwIcon, ShieldCheckIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import { type Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { Button } from "@/components/ui/button";

import { useFormatters } from "@/lib/i18n/formatters";
import type { SecretProposalMetadata } from "./use-secret-proposals-queue";

import type { useSecretProposalsQueue } from "./use-secret-proposals-queue";
export type SecretProposalsQueueProps = ReturnType<typeof useSecretProposalsQueue>;

export function SecretProposalsQueue({
  list,
  rows,
  totalCount,
  nextCursor,
  loading,
  stale,
  error,
  onRetry,
  onRefresh,
}: SecretProposalsQueueProps) {
  const t = useTranslations("lists.secretProposalsQueue");
  const presentation = useTranslations("approvals.secretProposalDetail.presentation");
  const fmt = useFormatters();
  const operations: Record<string, string> = {
    set: "operations.set",
    delete: "operations.delete",
    attach_bundle: "operations.attachBundle",
    detach_bundle: "operations.detachBundle",
    set_metadata: "operations.setMetadata",
  };
  const columns: Column<SecretProposalMetadata>[] = [
    {
      id: "app",
      header: t("paging.app"),
      cell: (row) => (
        <span className="flex min-w-0 items-center gap-2">
          <KeyIcon className="size-4 shrink-0" />
          <code className="truncate" title={row.registeredAppSlug}>
            {row.registeredAppSlug}
          </code>
        </span>
      ),
    },
    {
      id: "environment",
      header: t("paging.environment"),
      cell: (row) => (
        <code className="block truncate" title={row.environmentName}>
          {row.environmentName || t("row.appWide")}
        </code>
      ),
    },
    {
      id: "operation",
      header: t("paging.operation"),
      cell: (row) =>
        Object.hasOwn(operations, row.op) ? presentation(operations[row.op]) : row.op,
    },
    {
      id: "approvals",
      header: t("paging.approvals"),
      cell: (row) =>
        t("row.approvalsCount", {
          received: row.approvalsCount,
          required: row.requiredApproverCount,
        }),
    },
    {
      id: "proposer",
      header: t("paging.proposer"),
      cell: (row) => (
        <span className="block truncate" title={row.proposerDisplayName}>
          {row.proposerDisplayName || "—"}
        </span>
      ),
    },
    {
      id: "created",
      header: t("paging.created"),
      cell: (row) =>
        Number.isFinite(Date.parse(row.createdAt))
          ? fmt.formatDateTime(row.createdAt)
          : t("paging.unknown"),
    },
  ];
  const localizedList = {
    ...list,
    isFiltered: false,
    state: { ...list.state, q: "", filters: {}, sort: [] },
    definition: {
      ...list.definition,
      views: list.definition.views.map((view) => ({
        ...view,
        label: presentation("statuses.pending"),
      })),
    },
  };
  return (
    <ListPage
      header={{
        title: t("title"),
        crumbs: [{ label: t("title"), href: "/approvals/secret" }],
        primaryAction: (
          <Button variant="outline" onClick={onRefresh}>
            <RefreshCwIcon className="size-4" />
            {t("paging.refresh")}
          </Button>
        ),
      }}
      list={localizedList}
      label={t("title")}
      rows={rows}
      totalCount={totalCount}
      nextCursor={nextCursor}
      loading={loading}
      stale={stale}
      error={error}
      onRetry={onRetry}
      columns={columns}
      getRowId={(row) => row.id}
      rowHref={(row) => `/approvals/secret/${row.id}`}
      notice={
        <p role={error ? "status" : undefined} className="text-sm">
          {t(error ? "paging.recovery" : "paging.description")}
        </p>
      }
      empty={{
        title: t("empty.title"),
        description: t("empty.description"),
        icon: <ShieldCheckIcon className="size-6" />,
      }}
    />
  );
}
