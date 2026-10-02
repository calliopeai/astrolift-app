"use client";

import { previewHasAvailableBinding } from "./preview-binding";

import { ExternalLinkIcon, GitPullRequestIcon, TrashIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { appsListCrumbs } from "@/components/screens/deployments/apps-area";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import type { AstroliftPreviewEnvironment } from "@/graphql/lifecycle/lifecycle.types";
import { useFormatters } from "@/lib/i18n/formatters";

import { PREVIEW_DOT } from "./previews-list";
import type { usePreviews } from "./use-previews";

export type PreviewsScreenProps = ReturnType<typeof usePreviews>;

// Cells holding their own link sit above the row's stretched link.
const ABOVE_ROW_LINK = "relative z-10";

/**
 * Apps › Previews (spec 44 §4.4, §5.1): every app's per-PR preview on the
 * shared list, views All · Mine, cursor paged; teardown in each row's ⋯.
 * Pure view; the data half is usePreviews.
 */
export function PreviewsScreen({
  list,
  rows,
  loading,
  stale,
  error,
  onRetry,
  nextCursor,
  totalCount,
  canTearDown,
  tearingDown,
  tearDown,
}: PreviewsScreenProps) {
  const t = useTranslations("lists.previews");
  const fmt = useFormatters();
  const [tearTarget, setTearTarget] = React.useState<AstroliftPreviewEnvironment | null>(null);

  const columns: Column<AstroliftPreviewEnvironment>[] = [
    {
      id: "preview",
      header: t("columns.app"),
      cellClassName: "max-w-80",
      // The status dot folds into the linking cell: a link whose only
      // content is a dot has no name.
      cell: (p) => (
        <span className="flex min-w-0 items-start gap-2">
          <StatusDot status={PREVIEW_DOT[p.status]} className="mt-1.5 shrink-0" />
          <span className="block min-w-0">
            <span className="block truncate font-medium" title={p.registeredAppSlug}>
              {p.registeredAppSlug} <span className="font-mono">#{p.prNumber}</span>
            </span>
            <span
              className="text-muted-foreground block truncate font-mono text-xs"
              title={p.namespace}
            >
              {p.namespace}
            </span>
          </span>
        </span>
      ),
    },
    {
      id: "branch",
      header: t("columns.branch"),
      cellClassName: "max-w-64",
      cell: (p) => (
        <span className="block min-w-0">
          <span className="block truncate font-mono text-xs" title={p.branch}>
            {p.branch}
          </span>
          {p.commitSha && (
            <span className="text-muted-foreground block font-mono text-xs">
              {p.commitSha.slice(0, 8)}
            </span>
          )}
        </span>
      ),
    },
    {
      id: "hostname",
      header: t("columns.hostname"),
      cellClassName: "max-w-72",
      cell: (p) =>
        p.status === "running" && previewHasAvailableBinding(p) ? (
          <a
            href={`https://${p.hostname}`}
            target="_blank"
            rel="noreferrer"
            title={p.hostname}
            className={`${ABOVE_ROW_LINK} flex min-w-0 items-center gap-1 font-mono text-xs hover:underline`}
          >
            <span className="min-w-0 truncate">{p.hostname}</span>
            <ExternalLinkIcon className="size-3 shrink-0" />
          </a>
        ) : (
          <span
            className="text-muted-foreground block truncate font-mono text-xs"
            title={p.hostname}
          >
            {p.hostname}
          </span>
        ),
    },
    {
      id: "status",
      header: t("columns.status"),
      cell: (p) => (
        <Badge variant="secondary" className="capitalize">
          {t(`status.${p.status}`)}
        </Badge>
      ),
    },
    {
      id: "lastDeploy",
      header: t("columns.lastDeploy"),
      cell: (p) => (
        <span className="text-muted-foreground font-mono text-xs">
          {p.lastDeployedAt ? fmt.formatDateTime(p.lastDeployedAt) : "—"}
        </span>
      ),
    },
  ];

  function rowActions(p: AstroliftPreviewEnvironment) {
    return (
      <>
        {p.status === "running" && previewHasAvailableBinding(p) && (
          <DropdownMenuItem asChild>
            <a href={`https://${p.hostname}`} target="_blank" rel="noreferrer">
              <ExternalLinkIcon className="size-4" />
              {t("openPreview")}
            </a>
          </DropdownMenuItem>
        )}
        {p.prUrl && (
          <DropdownMenuItem asChild>
            <a href={p.prUrl} target="_blank" rel="noreferrer">
              <GitPullRequestIcon className="size-4" />
              {t("openPullRequest")}
            </a>
          </DropdownMenuItem>
        )}
        {p.status !== "torn_down" && canTearDown && (
          <Can permission="app.deploy">
            <DropdownMenuItem
              variant="destructive"
              disabled={tearingDown}
              onSelect={() => setTearTarget(p)}
            >
              <TrashIcon className="size-4" />
              {t("tearDown")}
            </DropdownMenuItem>
          </Can>
        )}
      </>
    );
  }

  return (
    <>
      <ListPage<AstroliftPreviewEnvironment>
        header={{ crumbs: appsListCrumbs("previews"), title: t("title") }}
        list={list}
        label={t("pluralLabel")}
        columns={columns}
        rows={rows}
        getRowId={(p) => p.id}
        rowHref={(p) => `/previews/${p.id}`}
        rowActions={rowActions}
        loading={loading}
        stale={stale}
        error={error}
        onRetry={onRetry}
        empty={{
          icon: <GitPullRequestIcon className="size-5" />,
          title: t("emptyTitle"),
          description: t("emptyDescription"),
          actionHref: "/apps",
          actionLabel: t("openApps"),
        }}
        totalCount={totalCount}
        nextCursor={nextCursor}
      />

      <ConfirmDialog
        open={tearTarget !== null}
        onOpenChange={(next) => {
          if (!next) setTearTarget(null);
        }}
        title={tearTarget ? t("confirmTitle", { pr: tearTarget.prNumber }) : t("confirmFallback")}
        description={
          tearTarget
            ? t("confirmDescription", {
                namespace: tearTarget.namespace,
                hostname: tearTarget.hostname,
                branch: tearTarget.branch,
              })
            : t("confirmDescriptionFallback")
        }
        confirmLabel={t("tearDown")}
        destructive
        onConfirm={async () => {
          if (tearTarget) await tearDown(tearTarget);
        }}
      />
    </>
  );
}
