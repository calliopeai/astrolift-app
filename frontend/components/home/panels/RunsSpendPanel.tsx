"use client";

/**
 * Runs & spend (spec 44 §4.3): the last seven days of agent runs, and of
 * the organization's spend when the viewer may read billing, as compact
 * bars with their totals in mono. Runs are counted from the newest runs
 * the Runs page reads, so the panel says when the week holds more.
 */

import { CoinsIcon } from "lucide-react";

import { Panel } from "@/components/panel/Panel";

import { homePanelTitle, type HomePanelProps } from "../registry";
import { useHomePresentation } from "./use-home-presentation";
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
  const { t, number, money } = useHomePresentation();
  const runs = days.reduce((n, d) => n + d.runs, 0);
  const failed = days.reduce((n, d) => n + d.failed, 0);
  const plus = capped ? "+" : "";
  return (
    <Panel
      title={homePanelTitle(panel, t)}
      icon={<CoinsIcon className="size-4" />}
      description={t("copy.last7Days")}
      span={panel.span}
      loading={loading}
      error={error}
      onRetry={onRetry}
    >
      <div className="space-y-4">
        <ChartSeries
          label={t("copy.runsPerDay")}
          value={`${number(runs)}${plus}`}
          hint={t("copy.sevenDays")}
          tone="primary"
          kind="bars"
          data={days.map((d) => d.runs)}
        />
        <ChartSeries
          label={t("copy.failedPerDay")}
          value={`${number(failed)}${plus}`}
          hint={t("copy.sevenDays")}
          tone="danger"
          kind="bars"
          data={days.map((d) => d.failed)}
        />
        {spend ? (
          <ChartSeries
            label={t("copy.spendPerDay")}
            value={money(spend.totalCents, spend.currency)}
            hint={t("copy.sevenDays")}
            tone="secondary"
            kind="bars"
            data={spend.perDay}
          />
        ) : (
          spendError && (
            <p className="text-muted-foreground font-mono text-xs [overflow-wrap:anywhere]">
              {t("copy.spendReadFailed", { message: spendError })}
            </p>
          )
        )}
        {capped && <p className="text-muted-foreground text-xs">{t("copy.cappedRuns")}</p>}
      </div>
    </Panel>
  );
}

/** Registered on Home as `runs-spend`. */
export function RunsSpendPanel({ panel }: HomePanelProps) {
  return <RunsSpendPanelView panel={panel} {...useRunsSpend()} />;
}
