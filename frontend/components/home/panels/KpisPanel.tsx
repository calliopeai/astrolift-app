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

import type { HomePanelProps } from "../registry";
import type { KpiFigure, KpisPanelData } from "./builder-operator-model";
import { useKpisPanel } from "./use-home-panels";

export interface KpisPanelViewProps extends KpisPanelData {
  panel: HomePanelProps["panel"];
}

function Figure({ figure }: { figure: KpiFigure }) {
  return (
    <li className="min-w-0">
      <Link
        href={figure.href}
        className="hover:bg-accent/40 focus-visible:ring-ring block min-w-0 rounded-sm px-2 py-1 transition-colors focus-visible:ring-2 focus-visible:outline-none"
      >
        <span className="text-muted-foreground block text-xs">
          {figure.label}
          {figure.hint && <span className="sr-only"> ({figure.hint})</span>}
        </span>
        <span className="block truncate font-mono text-xl tabular-nums" title={figure.hint}>
          {figure.value ?? "—"}
        </span>
        {figure.hint && (
          <span className="text-muted-foreground text-2xs block truncate" aria-hidden>
            {figure.hint}
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
  const nothingYet = figures.every((f) => f.value === null);
  return (
    <Panel
      title={panel.title}
      icon={<GaugeIcon className="size-4" />}
      description={<span className="font-mono">last {windowDays} days · spend month to date</span>}
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
              title: "No figures for your access",
              description: "Deploy, run and spend figures show for the areas you can see.",
            }
          : null
      }
    >
      <ul
        aria-label={`${panel.title}, last ${windowDays} days`}
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
