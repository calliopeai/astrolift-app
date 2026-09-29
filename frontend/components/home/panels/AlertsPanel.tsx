"use client";

/**
 * Alerts (spec 44 §4.3, Operator): the alerts firing now, the worst first,
 * as a ListSummary to Admin › Alerts › Events, Firing. Each line is the
 * alert's summary, its severity and when it fired. Pure view; the data is
 * useAlertsPanel.
 */

import { BellIcon, BellOffIcon } from "lucide-react";

import { ListSummary } from "@/components/list/ListSummary";
import type { AlertEvent } from "@/components/screens/alerts/use-alerts";
import { StatusDot } from "@/components/StatusDot";
import { formatRelativeAge } from "@/lib/format";

import type { HomePanelProps } from "../registry";
import type { HomeRead } from "./home-reads";
import { useAlertsPanel } from "./use-home-panels";

export interface AlertsPanelViewProps extends HomeRead {
  panel: HomePanelProps["panel"];
  /** Firing first, then acknowledged; worst severity, then newest. */
  rows: AlertEvent[];
  /** How many are firing, when the server says. */
  count: number | null;
}

const SEVERITY_DOT: Record<string, "ok" | "warn" | "error" | "muted"> = {
  critical: "error",
  error: "error",
  warn: "warn",
  warning: "warn",
  info: "ok",
};

function AlertLine({ alert }: { alert: AlertEvent }) {
  return (
    <span className="flex min-w-0 items-start gap-2">
      <StatusDot status={SEVERITY_DOT[alert.severity] ?? "muted"} className="mt-1.5" />
      <span className="min-w-0 flex-1">
        <span className="block truncate" title={alert.summary}>
          {alert.summary}
        </span>
        <span className="text-muted-foreground flex min-w-0 items-center gap-2 text-xs">
          <span className="font-mono">{alert.severity}</span>
          <span>{alert.acknowledgedAt ? "acknowledged" : "firing"}</span>
        </span>
      </span>
      <span className="text-muted-foreground shrink-0 font-mono text-xs">
        {formatRelativeAge(alert.firedAt)}
      </span>
    </span>
  );
}

export function AlertsPanelView({
  panel,
  rows,
  count,
  loading,
  error,
  onRetry,
}: AlertsPanelViewProps) {
  return (
    <ListSummary
      title={panel.title}
      icon={<BellIcon className="size-4" />}
      description="Firing now"
      span={panel.span}
      count={loading || error ? null : count}
      rows={rows}
      keyOf={(a) => a.id}
      renderRow={(a) => <AlertLine alert={a} />}
      rowHref={(a) => `/alerts/events/${encodeURIComponent(a.id)}`}
      viewAllHref={panel.href}
      loading={loading}
      error={error}
      onRetry={onRetry}
      empty={{
        icon: <BellOffIcon />,
        title: "No alerts firing",
        description: "Alerts show up here the moment a rule fires.",
        actionHref: "/alerts",
        actionLabel: "Alert rules",
      }}
    />
  );
}

/** Registered on Home as `alerts`. */
export function AlertsPanel({ panel }: HomePanelProps) {
  return <AlertsPanelView panel={panel} {...useAlertsPanel()} />;
}
