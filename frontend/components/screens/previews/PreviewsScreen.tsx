"use client";

import { ExternalLinkIcon, GitPullRequestIcon, TrashIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DataTable, type Column } from "@/components/data-table";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type {
  AstroliftPreviewEnvironment,
  PreviewStatus,
} from "@/graphql/lifecycle/lifecycle.types";
import { useFormatters } from "@/lib/i18n/formatters";

import type { usePreviews } from "./use-previews";

const statusToDot: Record<PreviewStatus, "ok" | "warn" | "error" | "muted" | "pending"> = {
  building: "pending",
  running: "ok",
  failed: "error",
  torn_down: "muted",
};

export type PreviewsScreenProps = ReturnType<typeof usePreviews>;

/** The /previews screen: every app's preview environments, with teardown. */
export function PreviewsScreen({ table, canTearDown, tearingDown, tearDown }: PreviewsScreenProps) {
  const t = useTranslations("lists.previews");
  const fmt = useFormatters();
  const formatTime = (iso: string | null | undefined): string =>
    iso ? fmt.formatDateTime(iso) : "—";

  const [tearTarget, setTearTarget] = React.useState<AstroliftPreviewEnvironment | null>(null);

  const columns: Column<AstroliftPreviewEnvironment>[] = [
    {
      id: "app",
      header: t("columns.app"),
      // The status dot lives in this cell rather than in a column of its
      // own. `rowHref` builds the row's link out of the first column, so
      // a lead column holding only a dot would give every row a link with
      // no accessible name.
      cell: (p) => (
        <>
          <span className="flex items-center gap-2 font-medium">
            <StatusDot status={statusToDot[p.status]} />
            {p.registeredAppSlug}
          </span>
          <span className="text-muted-foreground block font-mono text-xs">ns {p.namespace}</span>
        </>
      ),
    },
    {
      id: "pr",
      header: t("columns.pr"),
      cellClassName: "font-mono text-xs",
      cell: (p) => `#${p.prNumber}`,
    },
    {
      id: "branch",
      header: t("columns.branch"),
      cell: (p) => (
        <>
          <div className="text-sm">{p.branch}</div>
          {p.commitSha && (
            <div className="text-muted-foreground font-mono text-xs">{p.commitSha.slice(0, 7)}</div>
          )}
        </>
      ),
    },
    {
      id: "hostname",
      header: t("columns.hostname"),
      // Above the row link, so the running preview stays reachable.
      cellClassName: "relative z-10",
      cell: (p) =>
        p.status === "running" ? (
          <a
            href={`https://${p.hostname}`}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-sm hover:underline"
          >
            {p.hostname}
            <ExternalLinkIcon className="size-3" />
          </a>
        ) : (
          <span className="text-muted-foreground font-mono text-xs">{p.hostname}</span>
        ),
    },
    {
      id: "lastDeploy",
      header: t("columns.lastDeploy"),
      cellClassName: "text-muted-foreground text-sm",
      cell: (p) => formatTime(p.lastDeployedAt),
    },
    {
      id: "status",
      header: t("columns.status"),
      cell: (p) => (
        <Badge variant="secondary" className="capitalize">
          {p.status.replace(/_/g, " ")}
        </Badge>
      ),
    },
    {
      id: "actions",
      header: t("columns.actions"),
      align: "right",
      cellClassName: "relative z-10",
      cell: (p) =>
        p.status !== "torn_down" && canTearDown ? (
          <Can permission="app.deploy">
            <Button
              size="sm"
              variant="outline"
              disabled={tearingDown}
              onClick={() => setTearTarget(p)}
            >
              <TrashIcon className="size-3" /> {t("tearDown")}
            </Button>
          </Can>
        ) : null,
    },
  ];

  return (
    <PageShell title={t("title")} description={t("description")}>
      <DataTable
        label="Preview environments"
        controller={table}
        columns={columns}
        getRowId={(p) => p.id}
        // A real link, so a preview can be opened in a new tab. The
        // hand-rolled row was `role="link"` on a <tr> driving
        // `router.push`, which middle-click and copy-link could not reach.
        rowHref={(p) => `/previews/${p.id}`}
        searchPlaceholder="Search by app, branch, host, commit, or status…"
        empty={{
          icon: <GitPullRequestIcon className="size-5" />,
          title: t("emptyTitle"),
          description: t("emptyDescription"),
          actionHref: "/apps",
          actionLabel: t("openApps"),
        }}
        emptyFiltered={{
          title: "No matching previews",
          description:
            "No preview matches that search. The server matches the app, branch, hostname, commit and status.",
        }}
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
          if (!tearTarget) return;
          await tearDown(tearTarget);
        }}
      />
    </PageShell>
  );
}
