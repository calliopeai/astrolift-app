"use client";

/**
 * Runs & spend (spec 44 §4.3): the last seven days of agent runs, and of
 * the organization's spend when the viewer may read billing, as compact
 * bars with their totals in mono. Runs are counted from the newest runs
 * the Runs page reads, so the panel says when the week holds more.
 */

import { CoinsIcon } from "lucide-react";

import { Panel } from "@/components/panel/Panel";

import type { HomePanelProps } from "../registry";
import type { RunDay } from "./apps-agents-model";
import { ChartSeries } from "./ChartSeries";
import type { HomeRead } from "./home-reads";
import { useRunsSpend } from "./use-runs-spend";

export interface SpendSeries {
  /** Cents per day, aligned with the run days. */
  perDay: number[];
  totalCents: number;
  currency: string;
}

export interface RunsSpendPanelViewProps extends HomeRead {
  panel: HomePanelProps["panel"];
  days: RunDay[];
  /** More runs exist than the window counted, so the totals are a floor. */
  capped: boolean;
  /** Null when the viewer may not read billing, or the spend read failed. */
  spend: SpendSeries | null;
  /** Why spend is missing, when the viewer may read it but it did not load. */
  spendError?: string | null;
}

function money(cents: number, currency: string): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    maximumFractionDigits: 0,
  }).format(cents / 100);
}

/** Pure. */
export function RunsSpendPanelView({
  panel,
  days,
  capped,
  spend,
  spendError,
  loading,
  error,
  onRetry,
}: RunsSpendPanelViewProps) {
  const runs = days.reduce((n, d) => n + d.runs, 0);
  const failed = days.reduce((n, d) => n + d.failed, 0);
  const plus = capped ? "+" : "";
  return (
    <Panel
      title={panel.title}
      icon={<CoinsIcon className="size-4" />}
      description="Last 7 days"
      span={panel.span}
      loading={loading}
      error={error}
      onRetry={onRetry}
    >
      <div className="space-y-4">
        <ChartSeries
          label="Agent runs per day"
          value={`${runs.toLocaleString("en-US")}${plus}`}
          hint="7 days"
          tone="primary"
          kind="bars"
          data={days.map((d) => d.runs)}
        />
        <ChartSeries
          label="Failed runs per day"
          value={`${failed.toLocaleString("en-US")}${plus}`}
          hint="7 days"
          tone="danger"
          kind="bars"
          data={days.map((d) => d.failed)}
        />
        {spend ? (
          <ChartSeries
            label="Organization spend per day"
            value={money(spend.totalCents, spend.currency)}
            hint="7 days"
            tone="secondary"
            kind="bars"
            data={spend.perDay}
          />
        ) : (
          spendError && (
            <p className="text-muted-foreground font-mono text-xs [overflow-wrap:anywhere]">
              Spend did not load: {spendError}
            </p>
          )
        )}
        {capped && (
          <p className="text-muted-foreground text-xs">
            Counted from the newest runs; the week holds more.
          </p>
        )}
      </div>
    </Panel>
  );
}

/** Registered on Home as `runs-spend`. */
export function RunsSpendPanel({ panel }: HomePanelProps) {
  return <RunsSpendPanelView panel={panel} {...useRunsSpend()} />;
}
