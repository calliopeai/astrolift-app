"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  CheckCircle2Icon,
  DownloadIcon,
  Loader2Icon,
  SaveIcon,
  ScrollTextIcon,
  Settings2Icon,
  XCircleIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
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
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { UPDATE_ORGANIZATION } from "@/graphql/identity/identity.mutations";
import type {
  AstroliftOrganization,
  MutationResult,
} from "@/graphql/identity/identity.types";
import { EXPORT_AUDIT_EVENTS } from "@/graphql/operations/operations.mutations";
import {
  GET_AUDIT_RETENTION,
  LIST_AUDIT_EVENTS_PAGE,
} from "@/graphql/operations/operations.queries";
import type {
  AstroliftAuditEvent,
  AstroliftAuditEventPage,
  AstroliftAuditExport,
  AstroliftAuditRetention,
  AuditExportFormat,
} from "@/graphql/operations/operations.types";
import { useFormatters } from "@/lib/i18n/formatters";

interface PageResp {
  astroliftAuditEventsPage: AstroliftAuditEventPage;
}

interface RetentionResp {
  astroliftAuditRetention: AstroliftAuditRetention;
}

interface ExportResp {
  exportAuditEvents: {
    ok: boolean;
    errors: Array<{ code: string; message: string; field: string | null }>;
    data: AstroliftAuditExport | null;
  };
}

const PAGE_SIZE_OPTIONS = [25, 50, 100, 200] as const;
const DEFAULT_PAGE_SIZE = 100;

// Server-side bound on Organization.audit_log_retention_days (spec
// ceiling ~7 years); mirrors the guard in updateOrganization.
const RETENTION_MIN_DAYS = 1;
const RETENTION_MAX_DAYS = 2557;

const decisionStyles: Record<string, { icon: React.ReactNode; cls: string }> = {
  ALLOW: {
    icon: <CheckCircle2Icon className="size-3" />,
    cls: "bg-success/15 text-success-fg",
  },
  DENY: {
    icon: <XCircleIcon className="size-3" />,
    cls: "bg-danger/15 text-danger-fg",
  },
  UNKNOWN: {
    icon: null,
    cls: "bg-zinc-500/15 text-zinc-700 dark:text-zinc-300",
  },
};

/**
 * Convert a yyyy-mm-dd input value to an ISO 8601 timestamp at UTC
 * midnight. Returns null for empty strings so the variable is dropped
 * from the query. The DateTime scalar on the server accepts ISO 8601.
 */
function dateInputToIso(value: string, endOfDay = false): string | null {
  if (!value) return null;
  const [y, m, d] = value.split("-").map((s) => Number.parseInt(s, 10));
  if (!y || !m || !d) return null;
  const ts = endOfDay ? Date.UTC(y, m - 1, d, 23, 59, 59, 999) : Date.UTC(y, m - 1, d, 0, 0, 0, 0);
  return new Date(ts).toISOString();
}

function todayIso(): string {
  const now = new Date();
  const y = now.getUTCFullYear();
  const m = String(now.getUTCMonth() + 1).padStart(2, "0");
  const d = String(now.getUTCDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

export function AuditClient() {
  const t = useTranslations("lists.audit");
  const fmt = useFormatters();

  const [actionFilter, setActionFilter] = React.useState("");
  const [decisionFilter, setDecisionFilter] = React.useState<string>("");
  const [fromDate, setFromDate] = React.useState<string>("");
  const [toDate, setToDate] = React.useState<string>("");
  const [activeRow, setActiveRow] = React.useState<AstroliftAuditEvent | null>(null);
  const [exporting, setExporting] = React.useState(false);
  const [pageSize, setPageSize] = React.useState<number>(DEFAULT_PAGE_SIZE);

  const variables = React.useMemo(
    () => ({
      limit: pageSize,
      after: null as string | null,
      action: actionFilter || null,
      decision: decisionFilter || null,
      actorId: null as string | null,
      createdAtGte: dateInputToIso(fromDate, false),
      createdAtLte: dateInputToIso(toDate, true),
      includeTotal: true,
    }),
    [actionFilter, decisionFilter, fromDate, toDate, pageSize]
  );

  const { data, loading, fetchMore } = useQuery<PageResp>(LIST_AUDIT_EVENTS_PAGE, {
    variables,
    pollInterval: 5000,
    fetchPolicy: "cache-and-network",
  });

  const { data: retentionData } = useQuery<RetentionResp>(GET_AUDIT_RETENTION, {
    fetchPolicy: "cache-first",
  });

  const [exportMutation] = useMutation<ExportResp>(EXPORT_AUDIT_EVENTS);

  const page = data?.astroliftAuditEventsPage;
  const list = page?.items ?? [];
  const nextCursor = page?.nextCursor ?? null;
  const totalCount = page?.totalCount ?? null;

  const handleLoadMore = React.useCallback(() => {
    if (!nextCursor) return;
    fetchMore({
      variables: { ...variables, after: nextCursor },
      updateQuery: (prev, { fetchMoreResult }) => {
        if (!fetchMoreResult) return prev;
        return {
          astroliftAuditEventsPage: {
            ...fetchMoreResult.astroliftAuditEventsPage,
            items: [
              ...prev.astroliftAuditEventsPage.items,
              ...fetchMoreResult.astroliftAuditEventsPage.items,
            ],
          },
        };
      },
    });
  }, [fetchMore, nextCursor, variables]);

  const handleExport = React.useCallback(
    async (format: AuditExportFormat) => {
      setExporting(true);
      const toastId = toast.loading(t("export.toastStart"));
      try {
        const result = await exportMutation({
          variables: {
            input: {
              format: format.toUpperCase(),
              action: variables.action,
              decision: variables.decision,
              actorId: variables.actorId,
              createdAtGte: variables.createdAtGte,
              createdAtLte: variables.createdAtLte,
            },
          },
        });
        const payload = result.data?.exportAuditEvents;
        if (!payload?.ok || !payload.data) {
          const msg = payload?.errors?.[0]?.message ?? t("export.toastFailure");
          toast.error(msg, { id: toastId });
          return;
        }
        toast.success(t("export.toastReady", { rows: payload.data.rowCount }), {
          id: toastId,
          action: {
            label: t("export.toastDownload"),
            onClick: () => {
              window.open(payload.data!.downloadUrl, "_blank", "noopener,noreferrer");
            },
          },
          duration: 30000,
        });
      } catch (err) {
        toast.error(err instanceof Error ? err.message : t("export.toastFailure"), {
          id: toastId,
        });
      } finally {
        setExporting(false);
      }
    },
    [exportMutation, t, variables]
  );

  const retentionDays = retentionData?.astroliftAuditRetention?.days;
  const description = retentionDays
    ? t("retentionDescription", { days: retentionDays })
    : t("description");

  const maxDate = todayIso();

  return (
    <PageShell
      title={t("title")}
      description={description}
      actions={
        <div className="flex items-center gap-2">
          <RetentionDialog currentDays={retentionDays ?? null} />
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="sm" disabled={exporting}>
                {exporting ? (
                  <Loader2Icon className="size-4 animate-spin" />
                ) : (
                  <DownloadIcon className="size-4" />
                )}
                {t("export.button")}
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onSelect={() => handleExport("csv")}>
                {t("export.formatCsv")}
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => handleExport("ndjson")}>
                {t("export.formatNdjson")}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      }
    >
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex flex-col gap-1">
          <Label htmlFor="audit-action-filter" className="text-xs">
            {t("filters.actionLabel")}
          </Label>
          <Input
            id="audit-action-filter"
            value={actionFilter}
            onChange={(e) => setActionFilter(e.target.value)}
            placeholder={t("filters.actionPlaceholder")}
            className="w-64"
          />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor="audit-decision-filter" className="text-xs">
            {t("filters.decisionLabel")}
          </Label>
          <Select
            value={decisionFilter || "ALL"}
            onValueChange={(v) => setDecisionFilter(v === "ALL" ? "" : v)}
          >
            <SelectTrigger id="audit-decision-filter" className="w-40">
              <SelectValue placeholder={t("filters.decisionLabel")} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="ALL">{t("filters.anyDecision")}</SelectItem>
              <SelectItem value="ALLOW">ALLOW</SelectItem>
              <SelectItem value="DENY">DENY</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor="audit-from-date" className="text-xs">
            {t("filters.fromLabel")}
          </Label>
          <Input
            id="audit-from-date"
            type="date"
            value={fromDate}
            onChange={(e) => setFromDate(e.target.value)}
            max={maxDate}
            className="w-44"
          />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor="audit-to-date" className="text-xs">
            {t("filters.toLabel")}
          </Label>
          <Input
            id="audit-to-date"
            type="date"
            value={toDate}
            onChange={(e) => setToDate(e.target.value)}
            max={maxDate}
            className="w-44"
          />
        </div>
        <div className="ml-auto flex items-end gap-3">
          <div className="flex flex-col gap-1">
            <Label htmlFor="audit-page-size" className="text-xs">
              {t("pageSizeLabel")}
            </Label>
            <Select value={String(pageSize)} onValueChange={(v) => setPageSize(Number(v))}>
              <SelectTrigger id="audit-page-size" className="w-24">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PAGE_SIZE_OPTIONS.map((n) => (
                  <SelectItem key={n} value={String(n)}>
                    {n}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {totalCount != null && (
            <div className="text-muted-foreground pb-2 text-xs">
              {t("filters.totalCount", { count: totalCount })}
            </div>
          )}
        </div>
      </div>

      <Card>
        <CardContent className="p-0">
          {loading && list.length === 0 ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : list.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<ScrollTextIcon className="size-5" />}
                title={t("emptyTitle")}
                description={t("emptyDescription")}
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("columns.when")}</TableHead>
                  <TableHead>{t("columns.actor")}</TableHead>
                  <TableHead>{t("columns.action")}</TableHead>
                  <TableHead>{t("columns.target")}</TableHead>
                  <TableHead>{t("columns.decision")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.map((row) => {
                  const style = decisionStyles[row.decision] ?? decisionStyles.UNKNOWN;
                  return (
                    <TableRow
                      key={row.id}
                      className="hover:bg-muted/40 cursor-pointer"
                      onClick={() => setActiveRow(row)}
                    >
                      <TableCell className="font-mono text-xs whitespace-nowrap">
                        {fmt.formatDateTime(row.occurredAt)}
                      </TableCell>
                      <TableCell>
                        <div className="text-sm">{row.actorDisplay || row.actorKind}</div>
                        <div className="text-muted-foreground text-xs">
                          {row.actorKind}
                          {row.actorId ? ` · ${row.actorId}` : ""}
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline" className="font-mono text-xs">
                          {row.action}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-sm">
                        {row.targetKind ? (
                          <div className="font-mono text-xs">
                            {row.targetKind}
                            {row.targetSlug ? `:${row.targetSlug}` : ""}
                            {row.targetId ? ` (${row.targetId})` : ""}
                          </div>
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </TableCell>
                      <TableCell>
                        <Badge
                          className={style.cls + " gap-1 px-2 py-0.5 text-xs"}
                          variant="secondary"
                        >
                          {style.icon}
                          {row.decision}
                        </Badge>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {nextCursor && (
        <div className="flex justify-center">
          <Button variant="outline" size="sm" onClick={handleLoadMore} disabled={loading}>
            {t("loadMore")}
          </Button>
        </div>
      )}

      <AuditDetailsSheet row={activeRow} onOpenChange={(open) => !open && setActiveRow(null)} />
    </PageShell>
  );
}

/**
 * Admin-only audit-retention editor. Reads the current window from the
 * page's GET_AUDIT_RETENTION (now sourced from the per-org
 * Organization.audit_log_retention_days column) and writes it through
 * the existing `updateOrganization` mutation — the same field the
 * /administration/organization settings page edits, so the two
 * surfaces never disagree. The server enforces ORG_UPDATE and the
 * 1..2557 range; the dialog mirrors the bound client-side and refetches
 * the retention query so the subtitle updates in place.
 */
function RetentionDialog({ currentDays }: { currentDays: number | null }) {
  const t = useTranslations("lists.audit");
  const { org } = useActiveOrg();
  const [open, setOpen] = React.useState(false);
  const [days, setDays] = React.useState("");

  // Seed the input from the current window when the dialog opens (an
  // open event, not an effect) so an in-flight edit isn't clobbered by
  // a background retention refetch, and so we never setState in effect.
  const handleOpenChange = React.useCallback(
    (next: boolean) => {
      if (next) setDays(currentDays != null ? String(currentDays) : "");
      setOpen(next);
    },
    [currentDays]
  );

  const [updateOrg, { loading: saving }] = useMutation<{
    updateOrganization: MutationResult<AstroliftOrganization>;
  }>(UPDATE_ORGANIZATION, {
    refetchQueries: [{ query: GET_AUDIT_RETENTION }],
    awaitRefetchQueries: true,
  });

  const parsed = Number.parseInt(days, 10);
  const valid =
    Number.isFinite(parsed) && parsed >= RETENTION_MIN_DAYS && parsed <= RETENTION_MAX_DAYS;

  const handleSave = React.useCallback(async () => {
    if (!org || !valid) return;
    try {
      const { data } = await updateOrg({
        variables: { input: { id: org.id, auditLogRetentionDays: parsed } },
      });
      const payload = data?.updateOrganization;
      if (payload?.ok) {
        toast.success(t("retention.toastSuccess", { days: parsed }));
        setOpen(false);
      } else {
        toast.error(payload?.errors?.[0]?.message ?? t("retention.toastFailure"));
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("retention.toastFailure"));
    }
  }, [org, valid, parsed, updateOrg, t]);

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          <Settings2Icon className="size-4" />
          {t("retention.button")}
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("retention.title")}</DialogTitle>
          <DialogDescription>{t("retention.help")}</DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <Label htmlFor="audit-retention-days">{t("retention.fieldLabel")}</Label>
          <Input
            id="audit-retention-days"
            type="number"
            min={RETENTION_MIN_DAYS}
            max={RETENTION_MAX_DAYS}
            value={days}
            onChange={(e) => setDays(e.target.value)}
            className="w-40"
          />
          <p className="text-muted-foreground text-xs">
            {t("retention.range", { min: RETENTION_MIN_DAYS, max: RETENTION_MAX_DAYS })}
          </p>
        </div>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="ghost" size="sm" disabled={saving}>
              {t("retention.cancel")}
            </Button>
          </DialogClose>
          <Button size="sm" onClick={handleSave} disabled={saving || !valid || !org}>
            {saving ? (
              <Loader2Icon className="size-4 animate-spin" />
            ) : (
              <SaveIcon className="size-4" />
            )}
            {saving ? t("retention.saving") : t("retention.save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function AuditDetailsSheet({
  row,
  onOpenChange,
}: {
  row: AstroliftAuditEvent | null;
  onOpenChange: (open: boolean) => void;
}) {
  const t = useTranslations("lists.audit");
  const fmt = useFormatters();
  const hasBefore = row?.before != null;
  const hasAfter = row?.after != null;
  const showDiff = hasBefore && hasAfter;

  return (
    <Sheet open={row !== null} onOpenChange={onOpenChange}>
      <SheetContent className="w-full max-w-2xl overflow-y-auto">
        {row && (
          <>
            <SheetHeader>
              <SheetTitle className="font-mono text-base">{row.action}</SheetTitle>
              <SheetDescription>
                {fmt.formatDateTime(row.occurredAt)} · {row.actorDisplay || row.actorKind} ·{" "}
                {row.decision}
              </SheetDescription>
            </SheetHeader>

            <div className="space-y-6 p-4">
              {row.targetKind && (
                <section>
                  <h3 className="mb-2 text-sm font-semibold">{t("sheet.targetSection")}</h3>
                  <div className="bg-muted/40 rounded-md p-3 font-mono text-xs">
                    {row.targetKind}
                    {row.targetSlug ? `:${row.targetSlug}` : ""}
                    {row.targetId ? ` (${row.targetId})` : ""}
                  </div>
                </section>
              )}

              {showDiff ? (
                <section className="grid grid-cols-1 gap-3 md:grid-cols-2">
                  <div>
                    <h3 className="mb-2 text-sm font-semibold">{t("sheet.beforeSection")}</h3>
                    <JsonViewer value={row.before} />
                  </div>
                  <div>
                    <h3 className="mb-2 text-sm font-semibold">{t("sheet.afterSection")}</h3>
                    <JsonViewer value={row.after} />
                  </div>
                </section>
              ) : hasAfter ? (
                <section>
                  <h3 className="mb-2 text-sm font-semibold">{t("sheet.afterSection")}</h3>
                  <JsonViewer value={row.after} />
                </section>
              ) : hasBefore ? (
                <section>
                  <h3 className="mb-2 text-sm font-semibold">{t("sheet.beforeSection")}</h3>
                  <JsonViewer value={row.before} />
                </section>
              ) : null}

              <section>
                <h3 className="mb-2 text-sm font-semibold">{t("sheet.payloadSection")}</h3>
                <p className="text-muted-foreground mb-2 text-xs">{t("sheet.redactedNote")}</p>
                <JsonViewer value={row.data} />
              </section>

              {row.requestId && (
                <section>
                  <h3 className="mb-2 text-sm font-semibold">{t("sheet.requestIdSection")}</h3>
                  <div className="bg-muted/40 rounded-md p-3 font-mono text-xs break-all">
                    {row.requestId}
                  </div>
                </section>
              )}
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}

/**
 * Plain-text JSON pretty-printer. Sufficient for compliance review —
 * not a full tree expander, but doesn't add a dependency for a
 * surface that's mostly cared-about as "what's in this row?"
 */
function JsonViewer({ value }: { value: unknown }) {
  const text = React.useMemo(() => {
    try {
      return JSON.stringify(value ?? null, null, 2);
    } catch {
      return String(value);
    }
  }, [value]);
  return (
    <pre className="bg-muted/40 max-h-80 overflow-auto rounded-md p-3 font-mono text-xs break-all whitespace-pre-wrap">
      {text}
    </pre>
  );
}
