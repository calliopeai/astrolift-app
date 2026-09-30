"use client";

import { CloudIcon, ExternalLinkIcon, PauseIcon, PlayIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { Column, EmptyStateSpec } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { appsListCrumbs } from "@/components/screens/deployments/apps-area";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";

import type { EnvironmentsState } from "./use-environments";

export type EnvironmentsScreenProps = EnvironmentsState & {
  /** The agent's tab bar, when an agent's environments render this embedded. */
  tabs?: React.ReactNode;
};

// Cells holding their own link sit above the row's stretched link.
const ABOVE_ROW_LINK = "relative z-10";

/**
 * Apps › Environments (spec 44 §4.4, §5.1): every deploy target across apps
 * on the shared list, views All · Mine · Production · Previews, numbered
 * pages, pause and resume deploys in each row's ⋯. On an app's Settings tab
 * (and an agent's) the same list is embedded and its rows stay put, since
 * the global detail would leave the app. Pure view; the data half is
 * useEnvironments.
 */
export function EnvironmentsScreen({
  list,
  appSlug,
  tabs,
  rows,
  totalCount,
  loading,
  error,
  onRetry,
  busy,
  canPause,
  onPause,
  onResume,
}: EnvironmentsScreenProps) {
  const t = useTranslations("lists.environments");
  const [pauseTarget, setPauseTarget] = React.useState<AstroliftAppEnvironment | null>(null);

  const columns: Column<AstroliftAppEnvironment>[] = [
    {
      id: "environment",
      header: t("columns.appEnv"),
      sortKey: "name",
      cellClassName: "max-w-80",
      cell: (e) => (
        <span className="flex min-w-0 items-start gap-2">
          <StatusDot status={e.deploysPaused ? "warn" : "ok"} className="mt-1.5 shrink-0" />
          <span className="block min-w-0">
            <span className="block truncate font-mono text-sm font-medium" title={e.name}>
              {e.name}
            </span>
            <span
              className="text-muted-foreground block truncate text-xs"
              title={e.registeredAppSlug}
            >
              {e.registeredAppSlug}
            </span>
          </span>
        </span>
      ),
    },
    {
      id: "url",
      header: t("columns.url"),
      cellClassName: "max-w-72",
      cell: (e) =>
        e.url ? (
          <a
            href={e.url}
            target="_blank"
            rel="noreferrer"
            title={e.url}
            className={`${ABOVE_ROW_LINK} flex min-w-0 items-center gap-1 text-sm hover:underline`}
          >
            <span className="min-w-0 truncate">{e.url}</span>
            <ExternalLinkIcon className="size-3 shrink-0" />
          </a>
        ) : (
          <span className="text-muted-foreground text-sm">—</span>
        ),
    },
    {
      id: "cluster",
      header: t("columns.cluster"),
      sortKey: "cluster",
      cellClassName: "max-w-48",
      cell: (e) =>
        e.clusterSlug ? (
          <Link
            href={`/clusters/${e.clusterSlug}`}
            className={`${ABOVE_ROW_LINK} block truncate font-mono text-xs hover:underline`}
            title={e.clusterSlug}
          >
            {e.clusterSlug}
          </Link>
        ) : (
          <span className="text-muted-foreground font-mono text-xs">—</span>
        ),
    },
    {
      id: "approvals",
      header: t("columns.approvals"),
      cell: (e) => <span className="font-mono text-xs">{e.requiredApprovals}</span>,
    },
    {
      id: "deploys",
      header: t("columns.status"),
      cell: (e) =>
        e.deploysPaused ? (
          <Badge variant="destructive" className="gap-1">
            <PauseIcon className="size-3" /> {t("paused")}
          </Badge>
        ) : (
          <Badge variant="secondary" className="gap-1">
            <PlayIcon className="size-3" /> {t("active")}
          </Badge>
        ),
    },
  ];

  function rowActions(e: AstroliftAppEnvironment) {
    return (
      <>
        {canPause && (
          <Can permission="app.deploy">
            {e.deploysPaused ? (
              <DropdownMenuItem disabled={busy} onSelect={() => void onResume(e)}>
                <PlayIcon className="size-4" />
                {t("resume")}
              </DropdownMenuItem>
            ) : (
              <DropdownMenuItem disabled={busy} onSelect={() => setPauseTarget(e)}>
                <PauseIcon className="size-4" />
                {t("pause")}
              </DropdownMenuItem>
            )}
          </Can>
        )}
        {e.url && (
          <DropdownMenuItem asChild>
            <a href={e.url} target="_blank" rel="noreferrer">
              <ExternalLinkIcon className="size-4" />
              {t("openUrl")}
            </a>
          </DropdownMenuItem>
        )}
        <DropdownMenuItem asChild>
          <Link href={`/apps/${e.registeredAppSlug}`}>
            <CloudIcon className="size-4" />
            {t("openApp")}
          </Link>
        </DropdownMenuItem>
      </>
    );
  }

  const empty: EmptyStateSpec = {
    icon: <CloudIcon className="size-5" />,
    title: t("emptyTitle"),
    description: t("emptyDescription"),
    actionHref: "/apps",
    actionLabel: t("openApps"),
  };

  const body = {
    list,
    label: t("title"),
    columns,
    rows,
    getRowId: (e: AstroliftAppEnvironment) => e.id,
    rowActions,
    loading,
    error,
    onRetry,
    empty,
    totalCount,
  };

  return (
    <>
      {appSlug ? (
        <div className="flex min-w-0 flex-1 flex-col gap-4">
          {tabs}
          <ListPage<AstroliftAppEnvironment> embedded {...body} />
        </div>
      ) : (
        <ListPage<AstroliftAppEnvironment>
          header={{ crumbs: appsListCrumbs("environments"), title: t("title") }}
          rowHref={(e) => `/environments/${e.id}`}
          {...body}
        />
      )}

      <ConfirmDialog
        open={pauseTarget !== null}
        onOpenChange={(next) => {
          if (!next) setPauseTarget(null);
        }}
        title={
          pauseTarget
            ? t("confirmPause.title", { app: pauseTarget.registeredAppSlug, env: pauseTarget.name })
            : t("confirmPause.fallbackTitle")
        }
        description={t("confirmPause.description")}
        confirmLabel={t("confirmPause.confirm")}
        destructive
        onConfirm={async () => {
          if (pauseTarget) await onPause(pauseTarget);
        }}
      />
    </>
  );
}
