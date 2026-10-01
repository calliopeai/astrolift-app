"use client";

/**
 * KPIs (spec 44 §4.3, Builder): one row of numbers, in mono, with the
 * period they cover.
 *
 *   KPIs · last 30 days
 *   Deploys 14 · Runs 212 · Success 97.0% · p95 3m 10s · Spend $41
 *
 * Each figure links to the page it summarizes. A figure the person may not
 * see is left out; one whose source has not answered is a dash. Pure.
 */

import { GaugeIcon } from "lucide-react";
import Link from "next/link";

import { Panel } from "@/components/panel/Panel";
import { Skeleton } from "@/components/ui/skeleton";

import { homePanelTitle, type HomePanelProps } from "../registry";
import { useHomePresentation } from "./use-home-presentation";
import type { KpiFigure, KpisPanelData } from "./builder-operator-model";
import { useKpisPanel } from "./use-home-panels";

export interface KpisPanelViewProps extends KpisPanelData {
  panel: HomePanelProps["panel"];
}

function Figure({ figure }: { figure: KpiFigure }) {
  const { t } = useHomePresentation();
  const labels = {
    deploys: "Deploys",
    runs: "Runs",
    success: "Success",
    p95: "p95",
    spend: "Spend",
  };
  const hints: Partial<Record<KpiFigure["key"], string>> = {
    runs: "agent runs",
    success: "of deploys",
    p95: "deploy time",
    spend: "month to date",
  };
  const label = figure.label === labels[figure.key] ? t(`kpi.${figure.key}`) : figure.label;
  const hint =
    figure.hint && figure.hint === hints[figure.key] ? t(`kpi.${figure.key}Hint`) : figure.hint;
  return (
    <li className="min-w-0">
      <Link
        href={figure.href}
        className="hover:bg-accent/40 focus-visible:ring-ring block min-w-0 rounded-sm px-2 py-1 transition-colors focus-visible:ring-2 focus-visible:outline-none"
      >
        <span className="text-muted-foreground block text-xs">
          {label}
          {hint && <span className="sr-only"> ({hint})</span>}
        </span>
        <span className="block truncate font-mono text-xl tabular-nums" title={hint}>
          {figure.value ?? "—"}
        </span>
        {hint && (
          <span className="text-muted-foreground text-2xs block truncate" aria-hidden>
            {hint}
          </span>
        )}
      </Link>
    </li>
  );
}

export function KpisPanelView({
  panel,
  windowDays,
  figures,
  loading = false,
  error,
  onRetry,
}: KpisPanelViewProps) {
  const { t } = useHomePresentation();
  const nothingYet = figures.every((f) => f.value === null);
  return (
    <Panel
      title={homePanelTitle(panel, t)}
      icon={<GaugeIcon className="size-4" />}
      description={<span className="font-mono">{t("copy.kpiWindow", { count: windowDays })}</span>}
      span={panel.span}
      loading={loading && nothingYet}
      skeleton={
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
          {figures.map((f) => (
            <Skeleton key={f.key} className="h-12 w-full" />
          ))}
        </div>
      }
      error={error}
      onRetry={onRetry}
      empty={
        figures.length === 0
          ? {
              icon: <GaugeIcon className="size-5" />,
              title: t("copy.noKpisTitle"),
              description: t("copy.noKpisDescription"),
            }
          : null
      }
    >
      <ul
        aria-label={t("copy.kpiAria", { panel: homePanelTitle(panel, t), count: windowDays })}
        className="grid min-w-0 grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3 lg:grid-cols-5"
      >
        {figures.map((f) => (
          <Figure key={f.key} figure={f} />
        ))}
      </ul>
    </Panel>
  );
}

/** Registered on Home as `kpis`. */
export function KpisPanel({ panel }: HomePanelProps) {
  return <KpisPanelView panel={panel} {...useKpisPanel()} />;
}
