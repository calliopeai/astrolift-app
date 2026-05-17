"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { GaugeIcon, TrendingUpIcon } from "lucide-react";
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
import {
  LIST_QUOTAS,
  REQUEST_QUOTA_INCREASE,
} from "@/graphql/billing/billing.queries";
import type {
  AstroliftQuota,
  AstroliftQuotaIncreaseRequest,
} from "@/graphql/billing/billing.types";
import type { MutationResult } from "@/graphql/identity/identity.types";

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
  if (used >= hard) return "bg-red-500";
  if (used >= soft) return "bg-amber-500";
  return "bg-emerald-500";
}

function softUtilization(used: number, soft: number): number {
  if (!soft || soft <= 0) return 0;
  return used / soft;
}

export function QuotasClient() {
  const t = useTranslations("lists.quotas");
  const { data, loading } = useQuery<Resp>(LIST_QUOTAS, {
    fetchPolicy: "cache-and-network",
  });
  const list = data?.astroliftQuotas ?? [];

  const [requestTarget, setRequestTarget] = React.useState<AstroliftQuota | null>(
    null,
  );

  const [submitRequest, requestState] = useMutation<{
    requestQuotaIncrease: MutationResult<AstroliftQuotaIncreaseRequest>;
  }>(REQUEST_QUOTA_INCREASE, {
    refetchQueries: [{ query: LIST_QUOTAS }],
    awaitRefetchQueries: true,
  });

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

  return (
    <PageShell title={t("title")} description={t("description")}>
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
    </PageShell>
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
