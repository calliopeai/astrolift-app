"use client";

/**
 * Traffic & errors (spec 44 §4.3): one of the viewer's apps over the last
 * 24 hours, requests and errors as two compact series with their figures
 * in mono. There is no traffic read across apps, so the panel draws one at
 * a time and lets the reader pick which, from the same five My apps shows.
 * The metrics come from the app's cluster; when they are not flowing the
 * panel says why, as the app's Logs & metrics tab does.
 */

import { ActivityIcon } from "lucide-react";

import { Panel } from "@/components/panel/Panel";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { formatSignalValue } from "@/components/observability/golden-signals-types";
import {
  type ObservabilityPanelReason,
  panelEmptyState,
} from "@/components/observability/panel-reason";

import type { HomePanelProps } from "../registry";
import { appHref, seriesFigures } from "./apps-agents-model";
import { ChartSeries } from "./ChartSeries";
import type { HomeRead } from "./home-reads";
import { useTrafficErrors } from "./use-traffic-errors";

export interface SignalSeries {
  values: number[];
  /** The backend's unit hint: `rps`, `ratio`, `seconds`, `percent`. */
  unit: string;
}

export interface TrafficErrorsPanelViewProps extends HomeRead {
  panel: HomePanelProps["panel"];
  /** The apps the reader can pick from; empty when they have none. */
  apps: { slug: string; name: string }[];
  appSlug: string | null;
  onAppChange: (slug: string) => void;
  traffic: SignalSeries | null;
  errors: SignalSeries | null;
  /** Why there is nothing to draw, when the cluster says. */
  reason: ObservabilityPanelReason | null;
}

const fmt = (s: SignalSeries | null, n: number | null) =>
  s && n !== null ? formatSignalValue(n, s.unit) : null;

/** Pure. */
export function TrafficErrorsPanelView({
  panel,
  apps,
  appSlug,
  onAppChange,
  traffic,
  errors,
  reason,
  loading,
  error,
  onRetry,
}: TrafficErrorsPanelViewProps) {
  const noApps = !loading && !error && apps.length === 0;
  const flat = !traffic?.values.length && !errors?.values.length;
  const quiet = !loading && !error && !noApps && (flat || (reason !== null && reason !== "OK"));
  const why = panelEmptyState(reason ?? "NO_DATA_YET", {
    thing: "requests",
    notConfigured: "This app's cluster has no Prometheus endpoint, so its traffic is not flowing.",
  });
  const t = seriesFigures(traffic?.values ?? []);
  const e = seriesFigures(errors?.values ?? []);
  return (
    <Panel
      title={panel.title}
      icon={<ActivityIcon className="size-4" />}
      description="Last 24 hours"
      span={panel.span}
      loading={loading}
      error={error}
      onRetry={onRetry}
      actions={
        apps.length > 0 && (
          <Select value={appSlug ?? undefined} onValueChange={onAppChange}>
            <SelectTrigger size="sm" className="w-40 min-w-0" aria-label="App">
              <SelectValue placeholder="Pick an app" />
            </SelectTrigger>
            <SelectContent>
              {apps.map((a) => (
                <SelectItem key={a.slug} value={a.slug}>
                  {a.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )
      }
      empty={
        noApps
          ? {
              icon: <ActivityIcon />,
              title: "No apps of yours yet",
              description: "Traffic for apps you hold a role on shows up here.",
              actionHref: panel.href,
              actionLabel: "Browse apps",
            }
          : quiet
            ? {
                icon: <ActivityIcon />,
                title: why.title,
                description: why.description,
                actionHref: appSlug ? appHref(appSlug) : panel.href,
                actionLabel: "Open the app",
              }
            : null
      }
    >
      <div className="space-y-4">
        <ChartSeries
          label="Requests"
          value={fmt(traffic, t.last)}
          hint="now"
          tone="primary"
          kind="line"
          data={traffic?.values ?? []}
        />
        <ChartSeries
          label="Errors"
          value={fmt(errors, e.last)}
          hint={
            e.mean !== null && errors ? `avg ${formatSignalValue(e.mean, errors.unit)}` : undefined
          }
          tone="danger"
          kind="line"
          data={errors?.values ?? []}
        />
      </div>
    </Panel>
  );
}

/** Registered on Home as `traffic-errors`. */
export function TrafficErrorsPanel({ panel }: HomePanelProps) {
  return <TrafficErrorsPanelView panel={panel} {...useTrafficErrors()} />;
}
