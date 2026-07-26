"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { GaugeIcon, LineChartIcon, TrendingUpIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Section } from "@/components/ui/section";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { RadialGauge, Sparkline } from "@/components/viz";
import {
  LIST_QUOTAS,
  QUOTA_USAGE_HISTORY,
  REQUEST_QUOTA_INCREASE,
} from "@/graphql/billing/billing.queries";
import type {
  AstroliftQuota,
  AstroliftQuotaIncreaseRequest,
  AstroliftQuotaUsagePoint,
} from "@/graphql/billing/billing.types";
import type { MutationResult } from "@/graphql/identity/identity.types";
import {
  FEATURE_FLAG_ADMIN_QUOTAS,
  useFeatureFlag,
} from "@/graphql/server/server.hooks";

interface Resp {
  astroliftQuotas: AstroliftQuota[];
}

const REQUEST_FACTOR_PRESETS = ["1.5", "2", "3", "5", "10"] as const;
const SOFT_LIMIT_REQUEST_THRESHOLD = 0.8;

function pct(used: number, hard: number): number {
  if (!hard) return 0;
  return Math.min(100, (used / hard) * 100);
}

function severity(used: number, soft: number, hard: number) {
  if (used >= hard) return "bg-danger";
  if (used >= soft) return "bg-warning";
  return "bg-success";
}

function softUtilization(used: number, soft: number): number {
  if (!soft || soft <= 0) return 0;
  return used / soft;
}

// Gauge arc colour, matching the row-bar severity but as a text token.
function gaugeTone(used: number, soft: number, hard: number): string {
  if (used >= hard) return "text-danger-fg";
  if (used >= soft) return "text-warning-fg";
  return "text-success-fg";
}

function QuotaGauge({ quota }: { quota: AstroliftQuota }) {
  const util = pct(quota.currentUsage, quota.hardLimit) / 100;
  return (
    <Card>
      <CardContent className="flex items-center gap-3 p-4">
        <RadialGauge
          value={util}
          size={56}
          className={gaugeTone(quota.currentUsage, quota.softLimit, quota.hardLimit)}
          ariaLabel={`${quota.resource} utilization`}
        />
        <div className="min-w-0">
          <div className="truncate text-sm font-medium">{quota.resource}</div>
          <div className="text-muted-foreground font-mono text-2xs tabular-nums">
            {quota.currentUsage} / {quota.hardLimit}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

export function QuotasClient() {
  const t = useTranslations("lists.quotas");
  const quotasEnabled = useFeatureFlag(FEATURE_FLAG_ADMIN_QUOTAS);
  const { data, loading } = useQuery<Resp>(LIST_QUOTAS, {
    fetchPolicy: "cache-and-network",
    skip: !quotasEnabled,
  });
  const list = data?.astroliftQuotas ?? [];

  const [requestTarget, setRequestTarget] = React.useState<AstroliftQuota | null>(
    null,
  );
  const [historyTarget, setHistoryTarget] = React.useState<AstroliftQuota | null>(
    null,
  );

  const [submitRequest, requestState] = useMutation<{
    requestQuotaIncrease: MutationResult<AstroliftQuotaIncreaseRequest>;
  }>(REQUEST_QUOTA_INCREASE, {
    refetchQueries: [{ query: LIST_QUOTAS }],
    awaitRefetchQueries: true,
  });

  // Screen gate: quotas ships behind `admin.quotas_enabled` (#1204). With the
  // flag off the screen is hidden — the nav entry is filtered out and a direct
  // hit renders nothing — mirroring the zentinelle.enabled surface gate. The
  // flag reads false until the server-info handshake resolves, and LIST_QUOTAS
  // is skipped while gated so no billing query fires.
  if (!quotasEnabled) {
    return null;
  }

  async function handleSubmitRequest(
    target: AstroliftQuota,
    factor: number,
    reason: string,
  ): Promise<boolean> {
    const { data } = await submitRequest({
      variables: {
        input: { quotaId: target.id, factor, reason },
      },
    });
    if (data?.requestQuotaIncrease.ok) {
      toast.success(t("request.toastSubmitted"));
      return true;
    }
    toast.error(
      data?.requestQuotaIncrease.errors?.[0]?.message ??
        t("request.toastFailed"),
    );
    return false;
  }

  // Most-utilized first, so the gauges surface pressure at a glance.
  const byUtilization = [...list].sort(
    (a, b) => pct(b.currentUsage, b.hardLimit) - pct(a.currentUsage, a.hardLimit),
  );

  return (
    <PageShell title={t("title")} description={t("description")}>
      {list.length > 0 && (
        <Section title={t("overviewTitle")} description={t("overviewDescription")}>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {byUtilization.map((q) => (
              <QuotaGauge key={q.id} quota={q} />
            ))}
          </div>
        </Section>
      )}

      <Card>
        <CardContent className="p-0">
          {loading && list.length === 0 ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
            </div>
          ) : list.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<GaugeIcon className="size-5" />}
                title={t("emptyTitle")}
                description={t("emptyDescription")}
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("columns.scope")}</TableHead>
                  <TableHead>{t("columns.resource")}</TableHead>
                  <TableHead>{t("columns.usage")}</TableHead>
                  <TableHead className="w-64">{t("columns.bar")}</TableHead>
                  <TableHead>{t("columns.limits")}</TableHead>
                  <TableHead className="text-right">
                    {t("columns.actions")}
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.map((q) => {
                  const utilization = softUtilization(
                    q.currentUsage,
                    q.softLimit,
                  );
                  const overSoft = utilization > SOFT_LIMIT_REQUEST_THRESHOLD;
                  const pending = q.pendingRequest;
                  return (
                    <TableRow key={q.id}>
                      <TableCell>
                        <Badge variant="outline">{q.scopeKind}</Badge>
                      </TableCell>
                      <TableCell className="font-mono text-xs">
                        {q.resource}
                      </TableCell>
                      <TableCell className="font-mono text-sm tabular-nums">
                        {q.currentUsage}
                      </TableCell>
                      <TableCell>
                        <div className="bg-muted relative h-2 overflow-hidden rounded-full">
                          <div
                            className={`absolute inset-y-0 left-0 ${severity(q.currentUsage, q.softLimit, q.hardLimit)}`}
                            style={{
                              width: `${pct(q.currentUsage, q.hardLimit)}%`,
                            }}
                          />
                        </div>
                      </TableCell>
                      <TableCell className="text-muted-foreground font-mono text-xs">
                        {q.softLimit} / {q.hardLimit}
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="flex items-center justify-end gap-2">
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => setHistoryTarget(q)}
                          >
                            <LineChartIcon className="size-3.5" />
                            {t("history.button")}
                          </Button>
                          {pending ? (
                            <Badge
                              variant="secondary"
                              className="font-normal"
                            >
                              {t("request.pendingBadge", {
                                factor: pending.requestedFactor,
                                reason:
                                  pending.reason.length > 32
                                    ? `${pending.reason.slice(0, 32)}…`
                                    : pending.reason,
                              })}
                            </Badge>
                          ) : overSoft ? (
                            <Can permission="billing.read">
                              <Button
                                variant="outline"
                                size="sm"
                                onClick={() => setRequestTarget(q)}
                                disabled={requestState.loading}
                              >
                                <TrendingUpIcon className="size-3.5" />
                                {t("request.buttonRequest")}
                              </Button>
                            </Can>
                          ) : null}
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <RequestBumpSheet
        key={requestTarget?.id ?? "none"}
        target={requestTarget}
        onOpenChange={(next) => {
          if (!next) setRequestTarget(null);
        }}
        onSubmit={async (factor, reason) => {
          if (requestTarget) {
            const ok = await handleSubmitRequest(requestTarget, factor, reason);
            if (ok) setRequestTarget(null);
          }
        }}
        busy={requestState.loading}
      />

      <QuotaHistorySheet
        key={historyTarget?.id ?? "none-history"}
        target={historyTarget}
        onOpenChange={(next) => {
          if (!next) setHistoryTarget(null);
        }}
      />
    </PageShell>
  );
}

interface HistoryResp {
  astroliftQuotaUsageHistory: AstroliftQuotaUsagePoint[];
}

const HISTORY_WINDOWS = ["30", "90"] as const;

function QuotaHistorySheet({
  target,
  onOpenChange,
}: {
  target: AstroliftQuota | null;
  onOpenChange: (open: boolean) => void;
}) {
  const t = useTranslations("lists.quotas.history");
  const [windowDays, setWindowDays] = React.useState<string>("90");

  const { data, loading } = useQuery<HistoryResp>(QUOTA_USAGE_HISTORY, {
    variables: { quotaId: target?.id ?? "", windowDays: Number(windowDays) },
    skip: target === null,
    fetchPolicy: "cache-and-network",
  });

  const points = data?.astroliftQuotaUsageHistory ?? [];
  const usedSeries = points.map((p) => p.used);
  const latest = points.length > 0 ? points[points.length - 1] : null;
  const peakUsed = points.length > 0 ? Math.max(...usedSeries) : null;

  return (
    <Sheet open={target !== null} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col sm:max-w-lg">
        <SheetHeader>
          <SheetTitle>
            {t("title", { resource: target?.resource ?? "" })}
          </SheetTitle>
          <SheetDescription>{t("description")}</SheetDescription>
        </SheetHeader>

        <div className="flex flex-1 flex-col gap-4 overflow-auto px-4 pb-4">
          <div className="flex items-center justify-between gap-2">
            <Label htmlFor="history-window">{t("windowLabel")}</Label>
            <Select value={windowDays} onValueChange={setWindowDays}>
              <SelectTrigger id="history-window" className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {HISTORY_WINDOWS.map((w) => (
                  <SelectItem key={w} value={w}>
                    {t("windowOption", { days: w })}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {loading && points.length === 0 ? (
            <Skeleton className="h-24 w-full" />
          ) : points.length === 0 ? (
            <EmptyState
              icon={<LineChartIcon className="size-5" />}
              title={t("emptyTitle")}
              description={t("emptyDescription")}
            />
          ) : (
            <>
              <Card>
                <CardContent className="space-y-2 p-4">
                  <div className="text-muted-foreground text-xs font-medium">
                    {t("usedTrend")}
                  </div>
                  <Sparkline
                    data={usedSeries}
                    width={520}
                    height={72}
                    variant="area"
                    className="text-chart-1 h-20 w-full"
                    ariaLabel={t("title", { resource: target?.resource ?? "" })}
                  />
                </CardContent>
              </Card>

              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <HistoryStat label={t("latestUsed")} value={latest?.used ?? 0} />
                <HistoryStat label={t("peakUsed")} value={peakUsed ?? 0} />
                <HistoryStat
                  label={t("softLimit")}
                  value={target?.softLimit ?? 0}
                />
                <HistoryStat
                  label={t("hardLimit")}
                  value={target?.hardLimit ?? 0}
                />
              </div>
            </>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}

function HistoryStat({ label, value }: { label: string; value: number }) {
  return (
    <div className="bg-muted/40 rounded-md p-3">
      <div className="text-muted-foreground text-2xs">{label}</div>
      <div className="font-mono text-lg tabular-nums">{value}</div>
    </div>
  );
}

function RequestBumpSheet({
  target,
  onOpenChange,
  onSubmit,
  busy,
}: {
  target: AstroliftQuota | null;
  onOpenChange: (open: boolean) => void;
  onSubmit: (factor: number, reason: string) => Promise<void>;
  busy: boolean;
}) {
  const t = useTranslations("lists.quotas.request");
  const [factor, setFactor] = React.useState<string>("2");
  const [reason, setReason] = React.useState("");

  return (
    <Sheet open={target !== null} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col sm:max-w-md">
        <SheetHeader>
          <SheetTitle>{t("title")}</SheetTitle>
          <SheetDescription>{t("description")}</SheetDescription>
        </SheetHeader>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            const f = Number(factor);
            if (!Number.isFinite(f) || f <= 1) return;
            if (!reason.trim()) return;
            await onSubmit(f, reason.trim());
          }}
          className="flex flex-1 flex-col gap-4 overflow-auto px-4 pb-4"
        >
          <div className="space-y-2">
            <Label htmlFor="bump-factor">{t("factorLabel")}</Label>
            <Select value={factor} onValueChange={setFactor}>
              <SelectTrigger id="bump-factor">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {REQUEST_FACTOR_PRESETS.map((preset) => (
                  <SelectItem key={preset} value={preset}>
                    {`${preset}x`}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="bump-reason">{t("reasonLabel")}</Label>
            <Textarea
              id="bump-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder={t("reasonPlaceholder")}
              rows={4}
              required
            />
            <p className="text-muted-foreground text-xs">{t("reasonHint")}</p>
          </div>
          <SheetFooter className="flex-row justify-end gap-2 px-0">
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              {t("cancel")}
            </Button>
            <Button type="submit" disabled={busy || !reason.trim()}>
              {busy ? t("submitting") : t("submit")}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
