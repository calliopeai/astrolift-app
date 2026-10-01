"use client";

/**
 * Platform activity (spec 44 §4.3, Operator): the platform's own workflow
 * runs (deploys, onboarding, cluster bring-in, sweeps), newest first, as a
 * Feed that scrolls in its own frame and loads older runs as the reader
 * nears the end (rule 5). A failed run leads with its reason. Pure view;
 * the data is usePlatformActivityPanel.
 */

import { ActivityIcon } from "lucide-react";
import Link from "next/link";

import { Feed } from "@/components/feed/Feed";
import { Panel } from "@/components/panel/Panel";
import { StatusDot } from "@/components/StatusDot";
import type { AstroliftWorkflowRun } from "@/graphql/operations/operations.types";

import { homePanelTitle, type HomePanelProps } from "../registry";
import { useHomePresentation } from "./use-home-presentation";
import { platformRunReason } from "./builder-operator-model";
import { usePlatformActivityPanel } from "./use-home-panels";

export interface PlatformActivityPanelViewProps {
  panel: HomePanelProps["panel"];
  items: AstroliftWorkflowRun[];
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
  hasMore?: boolean;
  loadingMore?: boolean;
  onLoadMore?: () => void;
}

const STATUS_DOT: Record<string, "ok" | "warn" | "error" | "muted" | "pending"> = {
  running: "pending",
  completed: "ok",
  failed: "error",
  timed_out: "error",
  terminated: "warn",
  cancelled: "muted",
};

const BY_DAY = { day: (r: AstroliftWorkflowRun) => r.startedAt ?? r.endedAt ?? "" };

function RunItem({ run }: { run: AstroliftWorkflowRun }) {
  const { t, age, status } = useHomePresentation(true);
  const reason = platformRunReason(run, {
    timeout: t("copy.timedOut"),
    missing: t("copy.noReason"),
  });
  return (
    <div className="flex min-w-0 items-start gap-2 px-1">
      <StatusDot status={STATUS_DOT[run.status] ?? "muted"} className="mt-1.5" />
      <div className="min-w-0 flex-1">
        <p className="flex min-w-0 items-center gap-2 text-sm">
          <span className="min-w-0 flex-1 truncate font-mono" title={run.workflowKind}>
            {run.workflowKind}
          </span>
          <span className="text-muted-foreground shrink-0 text-xs">{status(run.status)}</span>
          {run.startedAt && (
            <time
              dateTime={run.startedAt}
              className="text-muted-foreground shrink-0 font-mono text-xs"
            >
              {age(run.startedAt)}
            </time>
          )}
        </p>
        <p className="text-muted-foreground truncate font-mono text-xs" title={run.workflowId}>
          {run.workflowId}
        </p>
        {reason && (
          <p className="text-danger-fg mt-0.5 line-clamp-2 font-mono text-xs [overflow-wrap:anywhere]">
            {reason}
          </p>
        )}
      </div>
    </div>
  );
}

export function PlatformActivityPanelView({
  panel,
  items,
  loading = false,
  error,
  onRetry,
  hasMore,
  loadingMore,
  onLoadMore,
}: PlatformActivityPanelViewProps) {
  const { t } = useHomePresentation();
  return (
    <Panel
      title={homePanelTitle(panel, t)}
      icon={<ActivityIcon className="size-4" />}
      span={panel.span}
      actions={
        <Link href={panel.href} className="text-primary text-xs font-medium hover:underline">
          Open
        </Link>
      }
    >
      <Feed
        label={homePanelTitle(panel, t)}
        items={items}
        keyOf={(r) => r.id}
        renderItem={(r) => <RunItem run={r} />}
        groupBy={BY_DAY}
        loading={loading}
        error={error}
        onRetry={onRetry}
        hasMore={hasMore}
        loadingMore={loadingMore}
        onLoadMore={onLoadMore}
        maxHeight="max-h-80"
        dense
        empty={{
          icon: <ActivityIcon className="size-5" />,
          title: t("copy.noPlatformTitle"),
          description: t("copy.noPlatformDescription"),
        }}
      />
    </Panel>
  );
}

/** Registered on Home as `platform-activity`. */
export function PlatformActivityPanel({ panel }: HomePanelProps) {
  return <PlatformActivityPanelView panel={panel} {...usePlatformActivityPanel()} />;
}
