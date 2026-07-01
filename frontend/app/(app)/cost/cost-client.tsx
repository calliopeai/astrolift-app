"use client";

import { useQuery } from "@apollo/client/react";
import {
  ArrowDownIcon,
  ArrowRightIcon,
  ArrowUpIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  CoinsIcon,
  TriangleAlertIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceDot,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  GET_COST_BY_BINDING,
  GET_COST_FORECAST,
  GET_COST_TREND,
  LIST_BUDGETS,
  LIST_COST_SNAPSHOTS,
} from "@/graphql/billing/billing.queries";
import type {
  AstroliftBudget,
  AstroliftCostAttribution,
  AstroliftCostForecast,
  AstroliftCostSnapshot,
  AstroliftCostTrendPoint,
  CostWindow,
} from "@/graphql/billing/billing.types";

interface BudgetsResp {
  astroliftBudgets: AstroliftBudget[];
}
interface CostResp {
  astroliftCostSnapshots: AstroliftCostSnapshot[];
}
interface TrendResp {
  astroliftCostTrend: AstroliftCostTrendPoint[];
}
interface ForecastResp {
  astroliftCostForecast: AstroliftCostForecast;
}
interface ByBindingResp {
  astroliftCostByBinding: AstroliftCostAttribution;
}

const WINDOW_OPTIONS: ReadonlyArray<{
  value: CostWindow;
  i18nKey: "tab24h" | "tab7d" | "tab30d" | "tabMtd";
}> = [
  { value: "H24", i18nKey: "tab24h" },
  { value: "D7", i18nKey: "tab7d" },
  { value: "D30", i18nKey: "tab30d" },
  { value: "MTD", i18nKey: "tabMtd" },
];

const DEFAULT_WINDOW: CostWindow = "D30";

function formatMoney(cents: number, currency: string): string {
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency,
    maximumFractionDigits: 0,
  }).format(cents / 100);
}

function formatMoneyDetailed(cents: number, currency: string): string {
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(cents / 100);
}

export function CostClient() {
  const t = useTranslations("lists.cost");
  const [window, setWindow] = React.useState<CostWindow>(DEFAULT_WINDOW);

  const budgets = useQuery<BudgetsResp>(LIST_BUDGETS, {
    fetchPolicy: "cache-and-network",
  });
  const costs = useQuery<CostResp>(LIST_COST_SNAPSHOTS, {
    variables: { window, limit: 500 },
    fetchPolicy: "cache-and-network",
  });
  const trend = useQuery<TrendResp>(GET_COST_TREND, {
    variables: { window },
    fetchPolicy: "cache-and-network",
  });
  const forecast = useQuery<ForecastResp>(GET_COST_FORECAST, {
    fetchPolicy: "cache-and-network",
  });
  const byBinding = useQuery<ByBindingResp>(GET_COST_BY_BINDING, {
    variables: { window },
    fetchPolicy: "cache-and-network",
  });

  const budgetList = budgets.data?.astroliftBudgets ?? [];
  const costList = costs.data?.astroliftCostSnapshots ?? [];
  const trendPoints = trend.data?.astroliftCostTrend ?? [];
  const forecastData = forecast.data?.astroliftCostForecast ?? null;
  const attribution = byBinding.data?.astroliftCostByBinding ?? null;
  const currency = costList[0]?.currency ?? forecastData?.currency ?? "USD";

  const byCategory = React.useMemo(() => {
    const m = new Map<string, number>();
    for (const c of costList) {
      m.set(c.by, (m.get(c.by) ?? 0) + c.amountCents);
    }
    return Array.from(m.entries()).sort((a, b) => b[1] - a[1]);
  }, [costList]);

  const totalCents = costList.reduce((sum, c) => sum + c.amountCents, 0);

  return (
    <PageShell title={t("title")} description={t("description")}>
      <CostWindowTabs value={window} onChange={setWindow} />

      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-muted-foreground text-sm">
              {t(`kpis.window.${windowLabelKey(window)}`)}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold tabular-nums">
              {costs.loading && totalCents === 0
                ? "…"
                : totalCents > 0
                  ? formatMoney(totalCents, currency)
                  : "—"}
            </p>
          </CardContent>
        </Card>

        <ForecastCard forecast={forecastData} loading={forecast.loading} t={t} />

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-muted-foreground text-sm">{t("kpis.budgets")}</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold tabular-nums">{budgetList.length}</p>
          </CardContent>
        </Card>
      </div>

      <TrendChart points={trendPoints} loading={trend.loading} currency={currency} t={t} />

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>{t("budgets.title")}</CardTitle>
            <CardDescription>{t("budgets.description")}</CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            {budgets.loading ? (
              <div className="space-y-2 p-6">
                <Skeleton className="h-12 w-full" />
              </div>
            ) : budgetList.length === 0 ? (
              <div className="p-6">
                <EmptyState
                  icon={<CoinsIcon className="size-5" />}
                  title={t("budgets.emptyTitle")}
                  description={t("budgets.emptyDescription")}
                />
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("budgets.columns.scope")}</TableHead>
                    <TableHead>{t("budgets.columns.period")}</TableHead>
                    <TableHead>{t("budgets.columns.spend")}</TableHead>
                    <TableHead>{t("budgets.columns.budget")}</TableHead>
                    <TableHead>{t("budgets.columns.alerts")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {budgetList.map((b) => {
                    const usagePct = b.amountCents
                      ? Math.min(100, (b.currentSpendCents / b.amountCents) * 100)
                      : 0;
                    return (
                      <TableRow key={b.id}>
                        <TableCell>
                          <Badge variant="outline">{b.scopeKind}</Badge>
                        </TableCell>
                        <TableCell>
                          <Badge variant="secondary">{b.period}</Badge>
                        </TableCell>
                        <TableCell className="font-mono text-sm">
                          {formatMoney(b.currentSpendCents, b.currency)}
                          <div className="bg-muted mt-1 h-1.5 overflow-hidden rounded-full">
                            <div
                              className={
                                usagePct >= 100
                                  ? "h-full bg-red-500"
                                  : usagePct >= 80
                                    ? "h-full bg-amber-500"
                                    : "h-full bg-emerald-500"
                              }
                              style={{ width: `${usagePct}%` }}
                            />
                          </div>
                        </TableCell>
                        <TableCell className="font-mono text-sm">
                          {formatMoney(b.amountCents, b.currency)}
                        </TableCell>
                        <TableCell className="text-muted-foreground text-xs">
                          {b.alertsAtPct.join("%, ")}%
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>{t("byCategory.title")}</CardTitle>
            <CardDescription>{t("byCategory.description")}</CardDescription>
          </CardHeader>
          <CardContent>
            {byCategory.length === 0 ? (
              <p className="text-muted-foreground text-sm">{t("byCategory.empty")}</p>
            ) : (
              <ul className="space-y-3">
                {byCategory.map(([cat, amount]) => (
                  <li key={cat} className="flex items-center justify-between">
                    <span className="text-sm capitalize">{cat.replace(/_/g, " ")}</span>
                    <span className="font-mono text-sm tabular-nums">
                      {formatMoney(amount, currency)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>

      <AttributionPanel
        attribution={attribution}
        loading={byBinding.loading}
        currency={currency}
        t={t}
      />
    </PageShell>
  );
}

// ---------------------------------------------------------------------
// Window tabs
// ---------------------------------------------------------------------

interface CostWindowTabsProps {
  value: CostWindow;
  onChange: (v: CostWindow) => void;
}

function CostWindowTabs({ value, onChange }: CostWindowTabsProps) {
  const t = useTranslations("lists.cost");
  return (
    <div
      className="bg-muted/40 inline-flex rounded-md border p-1"
      role="tablist"
      aria-label={t("tabsLabel")}
    >
      {WINDOW_OPTIONS.map((opt) => {
        const active = opt.value === value;
        return (
          <button
            key={opt.value}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(opt.value)}
            className={
              "rounded px-3 py-1 text-sm font-medium transition " +
              (active
                ? "bg-background text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground")
            }
          >
            {t(opt.i18nKey)}
          </button>
        );
      })}
    </div>
  );
}

function windowLabelKey(w: CostWindow): "h24" | "d7" | "d30" | "mtd" {
  switch (w) {
    case "H24":
      return "h24";
    case "D7":
      return "d7";
    case "D30":
      return "d30";
    case "MTD":
      return "mtd";
  }
}

// ---------------------------------------------------------------------
// Forecast KPI
// ---------------------------------------------------------------------

interface ForecastCardProps {
  forecast: AstroliftCostForecast | null;
  loading: boolean;
  t: ReturnType<typeof useTranslations<"lists.cost">>;
}

function ForecastCard({ forecast, loading, t }: ForecastCardProps) {
  if (loading && !forecast) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-muted-foreground text-sm">
            {t("kpis.projectedMonthly")}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-8 w-24" />
        </CardContent>
      </Card>
    );
  }
  if (!forecast || forecast.projectedMonthlyCents === 0) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-muted-foreground text-sm">
            {t("kpis.projectedMonthly")}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-2xl font-bold tabular-nums">—</p>
          <p className="text-muted-foreground mt-1 text-xs">{t("forecast.insufficientData")}</p>
        </CardContent>
      </Card>
    );
  }
  const delta = forecast.deltaPct;
  const Direction = delta > 0 ? ArrowUpIcon : delta < 0 ? ArrowDownIcon : ArrowRightIcon;
  const deltaColor =
    delta > 0
      ? "text-amber-600 dark:text-amber-400"
      : delta < 0
        ? "text-emerald-600 dark:text-emerald-400"
        : "text-muted-foreground";
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-muted-foreground text-sm">
          {t("kpis.projectedMonthly")}
        </CardTitle>
        <ConfidenceBadge value={forecast.confidence} t={t} />
      </CardHeader>
      <CardContent>
        <p className="text-2xl font-bold tabular-nums">
          {formatMoney(forecast.projectedMonthlyCents, forecast.currency)}
        </p>
        <div className={"mt-1 flex items-center gap-1 text-xs " + deltaColor}>
          <Direction className="size-3" />
          <span className="font-mono">
            {Math.abs(delta).toFixed(1)}% {t("forecast.vsPreviousMonth")}
          </span>
        </div>
        <p className="text-muted-foreground mt-1 text-xs">
          {t("forecast.mtdLine", {
            mtd: formatMoneyDetailed(forecast.mtdCents, forecast.currency),
            previous: formatMoneyDetailed(forecast.previousMonthCents, forecast.currency),
          })}
        </p>
      </CardContent>
    </Card>
  );
}

function ConfidenceBadge({
  value,
  t,
}: {
  value: AstroliftCostForecast["confidence"];
  t: ReturnType<typeof useTranslations<"lists.cost">>;
}) {
  const tone =
    value === "HIGH"
      ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"
      : value === "MEDIUM"
        ? "bg-amber-500/15 text-amber-700 dark:text-amber-300"
        : "bg-muted text-muted-foreground";
  const lowerKey = value.toLowerCase() as "low" | "medium" | "high";
  return (
    <span className={"rounded-full px-2 py-0.5 text-2xs font-medium " + tone}>
      {t(`forecast.confidence.${lowerKey}`)}
    </span>
  );
}

// ---------------------------------------------------------------------
// Trend chart
// ---------------------------------------------------------------------

interface TrendChartProps {
  points: AstroliftCostTrendPoint[];
  loading: boolean;
  currency: string;
  t: ReturnType<typeof useTranslations<"lists.cost">>;
}

function TrendChart({ points, loading, currency, t }: TrendChartProps) {
  const anomalies = points.filter((p) => p.isAnomaly);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between gap-2">
          <span>{t("trend.title")}</span>
          {anomalies.length > 0 && (
            <Badge
              variant="outline"
              className="border-amber-500/40 text-amber-700 dark:text-amber-300"
            >
              <TriangleAlertIcon className="mr-1 size-3" />
              {t("trend.anomaliesBadge", { count: anomalies.length })}
            </Badge>
          )}
        </CardTitle>
        <CardDescription>{t("trend.description")}</CardDescription>
      </CardHeader>
      <CardContent>
        {loading && points.length === 0 ? (
          <Skeleton className="h-56 w-full" />
        ) : points.length === 0 ? (
          <EmptyState
            icon={<CoinsIcon className="size-5" />}
            title={t("trend.emptyTitle")}
            description={t("trend.emptyDescription")}
          />
        ) : (
          <div className="h-56">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart
                data={points.map((p) => ({
                  date: p.date,
                  cents: p.amountCents,
                  isAnomaly: p.isAnomaly,
                }))}
              >
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="date" tick={{ fontSize: 10 }} minTickGap={32} />
                <YAxis
                  tick={{ fontSize: 10 }}
                  tickFormatter={(v: number) => formatMoney(v, currency).replace(/\.\d+$/, "")}
                  width={56}
                />
                <Tooltip
                  formatter={(v: number) => [
                    formatMoneyDetailed(v, currency),
                    t("trend.tooltipDailyTotal"),
                  ]}
                  labelFormatter={(label) => String(label)}
                />
                <Area
                  type="monotone"
                  dataKey="cents"
                  stroke="var(--chart-1)"
                  fill="var(--chart-1)"
                  fillOpacity={0.4}
                  isAnimationActive={false}
                />
                {anomalies.map((p) => (
                  <ReferenceDot
                    key={p.date}
                    x={p.date}
                    y={p.amountCents}
                    r={5}
                    fill="var(--destructive)"
                    stroke="var(--background)"
                    strokeWidth={2}
                    ifOverflow="extendDomain"
                  />
                ))}
              </AreaChart>
            </ResponsiveContainer>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------
// Per-resource attribution
// ---------------------------------------------------------------------

interface AttributionPanelProps {
  attribution: AstroliftCostAttribution | null;
  loading: boolean;
  currency: string;
  t: ReturnType<typeof useTranslations<"lists.cost">>;
}

function AttributionPanel({ attribution, loading, currency, t }: AttributionPanelProps) {
  const [expanded, setExpanded] = React.useState(true);

  if (loading && !attribution) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>{t("attribution.title")}</CardTitle>
          <CardDescription>{t("attribution.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-20 w-full" />
        </CardContent>
      </Card>
    );
  }

  const rows = attribution?.attributedRows ?? [];
  const total = attribution?.totalCents ?? 0;
  const unattributed = attribution?.unattributedCents ?? 0;
  const attributedPct = total > 0 ? Math.round(((total - unattributed) / total) * 100) : 0;

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-2">
          <div>
            <CardTitle>{t("attribution.title")}</CardTitle>
            <CardDescription>{t("attribution.description")}</CardDescription>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setExpanded((x) => !x)}
            aria-expanded={expanded}
            aria-controls="cost-attribution-body"
          >
            {expanded ? (
              <ChevronDownIcon className="size-4" />
            ) : (
              <ChevronRightIcon className="size-4" />
            )}
          </Button>
        </div>
        {total > 0 && (
          <p className="text-muted-foreground text-xs">
            {t("attribution.coverage", {
              pct: attributedPct,
              attributed: formatMoney(total - unattributed, currency),
              total: formatMoney(total, currency),
            })}
          </p>
        )}
      </CardHeader>
      {expanded && (
        <CardContent id="cost-attribution-body" className="p-0">
          {rows.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<TriangleAlertIcon className="size-5" />}
                title={t("attribution.emptyTitle")}
                description={t("attribution.emptyDescription")}
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("attribution.columns.resource")}</TableHead>
                  <TableHead>{t("attribution.columns.kind")}</TableHead>
                  <TableHead>{t("attribution.columns.app")}</TableHead>
                  <TableHead>{t("attribution.columns.category")}</TableHead>
                  <TableHead className="text-right">{t("attribution.columns.amount")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((r) => (
                  <TableRow key={`${r.managedServiceBindingId ?? "?"}-${r.by}`}>
                    <TableCell className="font-medium">
                      {r.managedServiceName ?? r.managedServiceBindingId ?? "—"}
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline">{r.managedServiceKind ?? "—"}</Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {r.registeredAppSlug ?? "—"}
                    </TableCell>
                    <TableCell className="capitalize">{r.by.replace(/_/g, " ")}</TableCell>
                    <TableCell className="text-right font-mono">
                      {formatMoney(r.amountCents, r.currency)}
                    </TableCell>
                  </TableRow>
                ))}
                {unattributed > 0 && (
                  <TableRow className="bg-muted/30">
                    <TableCell colSpan={4} className="text-muted-foreground italic">
                      {t("attribution.unattributedRow")}
                    </TableCell>
                    <TableCell className="text-right font-mono">
                      {formatMoney(unattributed, currency)}
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          )}
        </CardContent>
      )}
    </Card>
  );
}
