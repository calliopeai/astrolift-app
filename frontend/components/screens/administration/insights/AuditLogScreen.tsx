"use client";

import {
  CheckCircle2Icon,
  DownloadIcon,
  Loader2Icon,
  MoreHorizontalIcon,
  SaveIcon,
  ScrollTextIcon,
  Settings2Icon,
  XCircleIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { type Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import type { ListStateController } from "@/components/list/use-list-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import type { AstroliftAuditEvent, AuditExportFormat } from "@/graphql/operations/operations.types";
import { useFormatters } from "@/lib/i18n/formatters";

import { adminCrumbs } from "./header";

export interface AuditLogScreenProps {
  /** List state: views, search, filter chips, cursor (see audit-list.ts). */
  list: ListStateController;
  rows: AstroliftAuditEvent[];
  /** Events that arrived after the page was read, behind the "new" pill. */
  newRows?: { count: number; onReveal: () => void };
  loading: boolean;
  stale: boolean;
  error: { message: string } | null;
  onRetry: () => void;
  nextCursor: string | null;
  totalCount: number | null;
  /** A target-kind chip narrows the page in hand, not the query (no argument yet). */
  targetFilteredLocally: boolean;
  retentionDays: number | null;
  exporting: boolean;
  onExport: (format: AuditExportFormat) => void | Promise<void>;
  canEditRetention: boolean;
  savingRetention: boolean;
  saveRetention: (days: number) => Promise<boolean>;
}

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
    cls: "bg-foreground/5 text-muted-foreground",
  },
};

function targetText(row: AstroliftAuditEvent): string {
  return `${row.targetKind}${row.targetSlug ? `:${row.targetSlug}` : ""}${
    row.targetId ? ` (${row.targetId})` : ""
  }`;
}

/**
 * Admin › Usage & governance › Audit (spec 44 §5.1): the audit trail on the
 * list archetype. Views All · Mine · Denied; chips for actor, action, target
 * kind, decision and since; cursor paged, newest first, live on the first
 * page. A row opens the detail sheet; export and retention stay as they were.
 */
export function AuditLogScreen({
  list,
  rows,
  newRows,
  loading,
  stale,
  error,
  onRetry,
  nextCursor,
  totalCount,
  targetFilteredLocally,
  retentionDays,
  exporting,
  onExport: handleExport,
  canEditRetention,
  savingRetention,
  saveRetention,
}: AuditLogScreenProps) {
  const t = useTranslations("lists.audit");
  const fmt = useFormatters();

  const [activeRow, setActiveRow] = React.useState<AstroliftAuditEvent | null>(null);

  const columns: Column<AstroliftAuditEvent>[] = [
    {
      id: "when",
      header: t("columns.when"),
      cellClassName: "font-mono text-xs whitespace-nowrap",
      // Rows open the detail sheet; they are not links. One button per
      // row, stretched over it, named by action and time: what tells one
      // audit row from the next.
      cell: (row) => (
        <button
          type="button"
          aria-label={`${row.action} ${fmt.formatDateTime(row.occurredAt)}`}
          onClick={() => setActiveRow(row)}
          className="focus-visible:ring-ring rounded-sm text-left after:absolute after:inset-0 focus-visible:ring-2 focus-visible:outline-none"
        >
          {fmt.formatDateTime(row.occurredAt)}
        </button>
      ),
    },
    {
      id: "actor",
      header: t("columns.actor"),
      cellClassName: "max-w-64",
      cell: (row) => (
        <div className="min-w-0">
          <div className="truncate text-sm" title={row.actorDisplay || row.actorKind}>
            {row.actorDisplay || row.actorKind}
          </div>
          <div
            className="text-muted-foreground truncate font-mono text-xs"
            title={row.actorId || undefined}
          >
            {row.actorKind}
            {row.actorId ? ` · ${row.actorId}` : ""}
          </div>
        </div>
      ),
    },
    {
      id: "action",
      header: t("columns.action"),
      cellClassName: "max-w-72",
      cell: (row) => (
        <Badge
          variant="outline"
          className="block max-w-full truncate font-mono text-xs"
          title={row.action}
        >
          {row.action}
        </Badge>
      ),
    },
    {
      id: "target",
      header: t("columns.target"),
      cellClassName: "max-w-72 text-sm",
      cell: (row) =>
        row.targetKind ? (
          <div className="truncate font-mono text-xs" title={targetText(row)}>
            {targetText(row)}
          </div>
        ) : (
          <span className="text-muted-foreground">—</span>
        ),
    },
    {
      id: "decision",
      header: t("columns.decision"),
      cell: (row) => {
        const style = decisionStyles[row.decision] ?? decisionStyles.UNKNOWN;
        return (
          <Badge className={style.cls + " gap-1 px-2 py-0.5 font-mono text-xs"} variant="secondary">
            {style.icon}
            {row.decision}
          </Badge>
        );
      },
    },
  ];

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-3 p-6">
      <ListPage<AstroliftAuditEvent>
        header={{
          crumbs: adminCrumbs("audit", "Audit"),
          title: t("title"),
          context:
            retentionDays != null ? (
              <span className="font-mono">retained {retentionDays} days</span>
            ) : undefined,
          primaryAction: (
            <RetentionDialog
              currentDays={retentionDays}
              canSave={canEditRetention}
              saving={savingRetention}
              onSave={saveRetention}
            />
          ),
        }}
        list={list}
        label="Audit events"
        columns={columns}
        rows={rows}
        getRowId={(row) => row.id}
        rowClassName={() => "relative cursor-pointer"}
        loading={loading}
        stale={stale}
        error={error}
        onRetry={onRetry}
        empty={{
          icon: <ScrollTextIcon className="size-5" />,
          title: t("emptyTitle"),
          description: t("emptyDescription"),
        }}
        totalCount={totalCount}
        nextCursor={nextCursor}
        newRows={newRows}
        menu={
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                aria-label={t("export.button")}
                disabled={exporting}
              >
                {exporting ? (
                  <Loader2Icon className="size-4 animate-spin" />
                ) : (
                  <MoreHorizontalIcon className="size-4" />
                )}
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onSelect={() => handleExport("csv")}>
                <DownloadIcon className="size-4" />
                {t("export.formatCsv")}
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => handleExport("ndjson")}>
                <DownloadIcon className="size-4" />
                {t("export.formatNdjson")}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        }
      />

      {targetFilteredLocally && (
        <p className="text-muted-foreground text-xs">
          The target kind filter narrows this page only; the export covers every target.
        </p>
      )}

      <AuditDetailsSheet row={activeRow} onOpenChange={(open) => !open && setActiveRow(null)} />
    </div>
  );
}

/**
 * Admin-only audit-retention editor. Seeds from the current window
 * (sourced from the per-org Organization.audit_log_retention_days
 * column) and saves through the hook's `saveRetention`. The server
 * enforces ORG_UPDATE and the 1..2557 range; the dialog mirrors the
 * bound client-side.
 */
function RetentionDialog({
  currentDays,
  canSave,
  saving,
  onSave,
}: {
  currentDays: number | null;
  canSave: boolean;
  saving: boolean;
  onSave: (days: number) => Promise<boolean>;
}) {
  const t = useTranslations("lists.audit");
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

  const parsed = Number.parseInt(days, 10);
  const valid =
    Number.isFinite(parsed) && parsed >= RETENTION_MIN_DAYS && parsed <= RETENTION_MAX_DAYS;

  const handleSave = React.useCallback(async () => {
    if (!canSave || !valid) return;
    if (await onSave(parsed)) setOpen(false);
  }, [canSave, valid, parsed, onSave]);

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
          <Button size="sm" onClick={handleSave} disabled={saving || !valid || !canSave}>
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
