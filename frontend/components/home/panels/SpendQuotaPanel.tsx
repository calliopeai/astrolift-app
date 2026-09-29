"use client";

/**
 * Spend & quota (spec 44 §4.3, Operator): month-to-date spend as a compact
 * bar against the organization's budget, with a tick where the month is
 * projected to end and a legend under it.
 *
 *   $1,240 of $2,000 · 62%
 *   [██████████████▌·····|······]
 *   ■ Spent $1,240   | Projected $1,910   Budget $2,000 monthly
 *
 * Static: nothing moves, so reduced motion shows the same. Without a budget
 * it shows the spend and says there is no quota to draw it against. Pure
 * view; the data is useSpendQuotaPanel.
 */

import { CoinsIcon } from "lucide-react";

import { Panel } from "@/components/panel/Panel";
import { cn } from "@/lib/utils";

import type { HomePanelProps } from "../registry";
import {
  formatMoney,
  type MeterTone,
  type SpendQuotaData,
  spendMeter,
} from "./builder-operator-model";
import { useSpendQuotaPanel } from "./use-home-panels";

export interface SpendQuotaPanelViewProps extends SpendQuotaData {
  panel: HomePanelProps["panel"];
}

const FILL: Record<MeterTone, string> = {
  ok: "bg-chart-1",
  warn: "bg-warning",
  error: "bg-danger",
};

const pct = (n: number) => `${(n * 100).toFixed(0)}%`;

export function SpendQuotaPanelView({
  panel,
  forecast,
  budget,
  loading = false,
  error,
  budgetError,
  onRetry,
}: SpendQuotaPanelViewProps) {
  const meter = spendMeter(forecast, budget);
  const currency = forecast?.currency ?? budget?.currency ?? "USD";
  const spentCents = forecast?.mtdCents ?? budget?.currentSpendCents ?? 0;
  return (
    <Panel
      title={panel.title}
      icon={<CoinsIcon className="size-4" />}
      description="Month to date"
      span={panel.span}
      loading={loading && !forecast && !budget}
      error={!forecast && !budget ? error : null}
      onRetry={onRetry}
    >
      <div className="flex min-w-0 flex-col gap-3">
        <p className="flex min-w-0 flex-wrap items-baseline gap-x-2 font-mono tabular-nums">
          <span className="text-xl">{formatMoney(spentCents, currency)}</span>
          {meter && budget && (
            <span className="text-muted-foreground text-xs">
              of {formatMoney(budget.amountCents, budget.currency)} · {pct(meter.ratio)}
            </span>
          )}
        </p>
        {meter && budget ? (
          <>
            <div
              role="meter"
              aria-label={`${panel.title}: spend against budget`}
              aria-valuemin={0}
              aria-valuemax={budget.amountCents / 100}
              aria-valuenow={spentCents / 100}
              aria-valuetext={`${formatMoney(spentCents, currency)} of ${formatMoney(budget.amountCents, budget.currency)}`}
              className="bg-muted relative h-2 w-full min-w-0 overflow-hidden rounded-full"
            >
              <div
                className={cn("h-full rounded-full", FILL[meter.tone])}
                style={{ width: pct(meter.spent) }}
              />
              {meter.projected !== null && (
                <div
                  aria-hidden
                  className="bg-foreground absolute inset-y-0 w-0.5"
                  style={{ left: `calc(${pct(meter.projected)} - 1px)` }}
                />
              )}
            </div>
            <ul
              aria-label="Legend"
              className="text-muted-foreground flex min-w-0 flex-wrap gap-x-4 gap-y-1 text-xs"
            >
              <li className="inline-flex items-center gap-1.5">
                <span aria-hidden className={cn("size-2 rounded-full", FILL[meter.tone])} />
                Spent{" "}
                <span className="text-foreground font-mono">
                  {formatMoney(spentCents, currency)}
                </span>
              </li>
              {forecast && (
                <li className="inline-flex items-center gap-1.5">
                  <span aria-hidden className="bg-foreground h-3 w-0.5" />
                  Projected{" "}
                  <span className="text-foreground font-mono">
                    {formatMoney(forecast.projectedMonthlyCents, forecast.currency)}
                  </span>
                </li>
              )}
              <li className="inline-flex items-center gap-1.5">
                <span aria-hidden className="bg-muted size-2 rounded-full border" />
                Budget{" "}
                <span className="text-foreground font-mono">
                  {formatMoney(budget.amountCents, budget.currency)}
                </span>{" "}
                {budget.period}
              </li>
            </ul>
          </>
        ) : (
          <p className="text-muted-foreground text-xs [overflow-wrap:anywhere]">
            {budgetError
              ? `The budget did not load: ${budgetError}`
              : "No organization budget is set, so there is no quota to draw spend against."}
          </p>
        )}
      </div>
    </Panel>
  );
}

/** Registered on Home as `spend-quota`. */
export function SpendQuotaPanel({ panel }: HomePanelProps) {
  return <SpendQuotaPanelView panel={panel} {...useSpendQuotaPanel()} />;
}
