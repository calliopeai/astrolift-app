"use client";

import { useQuery } from "@apollo/client/react";
import { GaugeIcon } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { LIST_QUOTAS } from "@/graphql/billing/billing.queries";
import type { AstroliftQuota } from "@/graphql/billing/billing.types";

interface Resp {
  astroliftQuotas: AstroliftQuota[];
}

function pct(used: number, hard: number): number {
  if (!hard) return 0;
  return Math.min(100, (used / hard) * 100);
}

function severity(used: number, soft: number, hard: number) {
  if (used >= hard) return "bg-red-500";
  if (used >= soft) return "bg-amber-500";
  return "bg-emerald-500";
}

export default function QuotasPage() {
  const { data, loading } = useQuery<Resp>(LIST_QUOTAS);
  const list = data?.astroliftQuotas ?? [];

  return (
    <PageShell
      title="Quotas"
      description="Resource limits per scope. Soft limits trigger alerts; hard limits block the next admission attempt."
    >
      <Card>
        <CardContent className="p-0">
          {loading ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
            </div>
          ) : list.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<GaugeIcon className="size-5" />}
                title="No quotas configured"
                description="Quotas can be set per organization, team, or project. The default install ships with sensible org-level caps."
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Scope</TableHead>
                  <TableHead>Resource</TableHead>
                  <TableHead>Usage</TableHead>
                  <TableHead className="w-64">Bar</TableHead>
                  <TableHead>Soft / Hard</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.map((q) => (
                  <TableRow key={q.id}>
                    <TableCell>
                      <Badge variant="outline">{q.scopeKind}</Badge>
                    </TableCell>
                    <TableCell className="font-mono text-xs">{q.resource}</TableCell>
                    <TableCell className="font-mono text-sm tabular-nums">
                      {q.currentUsage}
                    </TableCell>
                    <TableCell>
                      <div className="bg-muted relative h-2 overflow-hidden rounded-full">
                        <div
                          className={`absolute inset-y-0 left-0 ${severity(q.currentUsage, q.softLimit, q.hardLimit)}`}
                          style={{ width: `${pct(q.currentUsage, q.hardLimit)}%` }}
                        />
                      </div>
                    </TableCell>
                    <TableCell className="text-muted-foreground font-mono text-xs">
                      {q.softLimit} / {q.hardLimit}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </PageShell>
  );
}
