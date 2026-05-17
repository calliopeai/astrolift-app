"use client";

import { useQuery } from "@apollo/client/react";
import { CoinsIcon } from "lucide-react";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
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
import { LIST_BUDGETS, LIST_COST_SNAPSHOTS } from "@/graphql/billing/billing.queries";
import type { AstroliftBudget, AstroliftCostSnapshot } from "@/graphql/billing/billing.types";

interface BudgetsResp {
  astroliftBudgets: AstroliftBudget[];
}
interface CostResp {
  astroliftCostSnapshots: AstroliftCostSnapshot[];
}

function formatMoney(cents: number, currency: string): string {
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency,
    maximumFractionDigits: 0,
  }).format(cents / 100);
}

export function CostClient() {
  const budgets = useQuery<BudgetsResp>(LIST_BUDGETS);
  const costs = useQuery<CostResp>(LIST_COST_SNAPSHOTS, {
    variables: { days: 30, limit: 100 },
  });

  const budgetList = budgets.data?.astroliftBudgets ?? [];
  const costList = costs.data?.astroliftCostSnapshots ?? [];

  // Aggregate cost by `by` (workload / managed_service / etc).
  const byCategory = React.useMemo(() => {
    const m = new Map<string, number>();
    for (const c of costList) {
      m.set(c.by, (m.get(c.by) ?? 0) + c.amountCents);
    }
    return Array.from(m.entries()).sort((a, b) => b[1] - a[1]);
  }, [costList]);

  const totalCents = costList.reduce((sum, c) => sum + c.amountCents, 0);

  return (
    <PageShell
      title="Cost"
      description="Currency-denominated platform spend, rolled up from CostSnapshot rows in the last 30 days."
    >
      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-muted-foreground text-sm">Last 30 days</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold tabular-nums">
              {totalCents > 0 ? formatMoney(totalCents, costList[0]?.currency ?? "USD") : "—"}
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-muted-foreground text-sm">Budgets</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold tabular-nums">{budgetList.length}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-muted-foreground text-sm">Snapshots</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold tabular-nums">{costList.length}</p>
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Budgets</CardTitle>
            <CardDescription>
              Budgets fire alerts as you cross each percentage threshold.
            </CardDescription>
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
                  title="No budgets configured"
                  description="Budgets can be set per organization, team, or project."
                />
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Scope</TableHead>
                    <TableHead>Period</TableHead>
                    <TableHead>Spend</TableHead>
                    <TableHead>Budget</TableHead>
                    <TableHead>Alerts</TableHead>
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
            <CardTitle>By category</CardTitle>
            <CardDescription>30-day cost split.</CardDescription>
          </CardHeader>
          <CardContent>
            {byCategory.length === 0 ? (
              <p className="text-muted-foreground text-sm">No cost data yet.</p>
            ) : (
              <ul className="space-y-3">
                {byCategory.map(([cat, amount]) => (
                  <li key={cat} className="flex items-center justify-between">
                    <span className="text-sm capitalize">{cat.replace(/_/g, " ")}</span>
                    <span className="font-mono text-sm tabular-nums">
                      {formatMoney(amount, costList[0]?.currency ?? "USD")}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>
    </PageShell>
  );
}
