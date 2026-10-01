"use client";

/**
 * Failing (spec 44 §4.3, decision 4; §5.2): what is broken now, failed
 * deploys and failed runs together, each row leading with its reason so
 * the reader knows what went wrong before opening it. A ListSummary.
 */

import { CircleXIcon, OctagonAlertIcon, PlayIcon, RocketIcon } from "lucide-react";
import * as React from "react";

import { ListSummary } from "@/components/list/ListSummary";

import { homePanelTitle, type HomePanelProps } from "../registry";
import { useHomePresentation } from "./use-home-presentation";
import type { HomeRead } from "./home-reads";
import { newestFirst } from "./apps-agents-model";
import { useFailing } from "./use-failing";

export interface FailingItem {
  key: string;
  kind: "deploy" | "run";
  /** Why, verbatim from the system: the first thing on the row. */
  reason: string;
  /** What failed: the app and environment, the agent. */
  subject: string;
  /** ISO time it failed. */
  at: string;
  href: string;
}

export interface FailingPanelViewProps extends HomeRead {
  panel: HomePanelProps["panel"];
  items: FailingItem[];
  /** Everything failing, when every source can say; null when one cannot. */
  count: number | null;
}

const KIND_ICON: Record<FailingItem["kind"], React.ReactNode> = {
  deploy: <RocketIcon className="size-3.5" />,
  run: <PlayIcon className="size-3.5" />,
};

export function FailingLine({ item }: { item: FailingItem }) {
  const { t, age } = useHomePresentation(true);
  return (
    <div className="min-w-0">
      <p className="text-danger-fg line-clamp-2 font-mono text-xs [overflow-wrap:anywhere]">
        {item.reason}
      </p>
      <p className="text-muted-foreground mt-0.5 flex min-w-0 items-center gap-1.5 text-xs">
        <span className="shrink-0" aria-hidden>
          {KIND_ICON[item.kind]}
        </span>
        <span className="sr-only">{t(item.kind === "deploy" ? "copy.deploy" : "copy.run")}: </span>
        <span className="text-foreground min-w-0 flex-1 truncate" title={item.subject}>
          {item.subject}
        </span>
        <span className="shrink-0 font-mono">{age(item.at)}</span>
      </p>
    </div>
  );
}

/** Pure. */
export function FailingPanelView({
  panel,
  items,
  count,
  loading,
  error,
  onRetry,
}: FailingPanelViewProps) {
  const { t } = useHomePresentation();
  return (
    <ListSummary
      title={homePanelTitle(panel, t)}
      icon={<OctagonAlertIcon className="size-4" />}
      span={panel.span}
      count={loading || error ? null : count}
      rows={newestFirst(items, (i) => i.at)}
      keyOf={(i) => i.key}
      renderRow={(i) => <FailingLine item={i} />}
      rowHref={(i) => i.href}
      viewAllHref={panel.href}
      loading={loading}
      error={error}
      onRetry={onRetry}
      empty={{
        icon: <CircleXIcon />,
        title: t("copy.nothingFailingTitle"),
        description: t("copy.nothingFailingDescription"),
      }}
    />
  );
}

/** Registered on Home as `failing`. */
export function FailingPanel({ panel }: HomePanelProps) {
  return <FailingPanelView panel={panel} {...useFailing()} />;
}
