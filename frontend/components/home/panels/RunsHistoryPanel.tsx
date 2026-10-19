"use client";

/**
 * The last seven days of agent runs as compact bars with their totals.
 * Runs are counted from the newest runs
 * the Runs page reads, so the panel says when the week holds more.
 */

import { ChartColumnIcon } from "lucide-react";

import { Panel } from "@/components/panel/Panel";

import { homePanelTitle, type HomePanelProps } from "../registry";
import { useHomePresentation } from "./use-home-presentation";
import type { RunDay } from "./apps-agents-model";
import { ChartSeries } from "./ChartSeries";
import type { HomeRead } from "./home-reads";
import { useRunsHistory } from "./use-runs-history";

export interface RunsHistoryPanelViewProps extends HomeRead {
  panel: HomePanelProps["panel"];
  days: RunDay[];
  /** More runs exist than the window counted, so the totals are a floor. */
  capped: boolean;
}

/** Pure. */
export function RunsHistoryPanelView({
  panel,
  days,
  capped,
  loading,
  error,
  onRetry,
}: RunsHistoryPanelViewProps) {
  const { t, number } = useHomePresentation();
  const runs = days.reduce((n, d) => n + d.runs, 0);
  const failed = days.reduce((n, d) => n + d.failed, 0);
  const plus = capped ? "+" : "";
  return (
    <Panel
      title={homePanelTitle(panel, t)}
      icon={<ChartColumnIcon className="size-4" />}
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
        {capped && <p className="text-muted-foreground text-xs">{t("copy.cappedRuns")}</p>}
      </div>
    </Panel>
  );
}

/** The legacy registry key preserves saved Home layouts. */
export function RunsHistoryPanel({ panel }: HomePanelProps) {
  return <RunsHistoryPanelView panel={panel} {...useRunsHistory()} />;
}
