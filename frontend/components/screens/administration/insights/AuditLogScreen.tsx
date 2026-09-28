"use client";

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

import { DataTable, type Column } from "@/components/data-table";
import { PageShell } from "@/components/PageShell";
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
import type { AstroliftAuditEvent } from "@/graphql/operations/operations.types";
import { useFormatters } from "@/lib/i18n/formatters";

import type { useAuditLog } from "./use-audit-log";

export type AuditLogScreenProps = ReturnType<typeof useAuditLog>;

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

function todayIso(): string {
  const now = new Date();
  const y = now.getUTCFullYear();
  const m = String(now.getUTCMonth() + 1).padStart(2, "0");
  const d = String(now.getUTCDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

/** The audit trail: filterable event table, detail sheet, export and retention editor. */
export function AuditLogScreen({
  table,
  decisionFilter,
  onDecisionFilterChange: setDecisionFilter,
  fromDate,
  onFromDateChange: setFromDate,
  toDate,
  onToDateChange: setToDate,
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

  const description = retentionDays
    ? t("retentionDescription", { days: retentionDays })
    : t("description");

  const maxDate = todayIso();

  const columns: Column<AstroliftAuditEvent>[] = [
    {
      id: "when",
      header: t("columns.when"),
      cellClassName: "font-mono text-xs whitespace-nowrap",
      cell: (row) => fmt.formatDateTime(row.occurredAt),
    },
    {
      id: "actor",
      header: t("columns.actor"),
      cell: (row) => (
        <>
          <div className="text-sm">{row.actorDisplay || row.actorKind}</div>
          <div className="text-muted-foreground text-xs">
            {row.actorKind}
            {row.actorId ? ` · ${row.actorId}` : ""}
          </div>
        </>
      ),
    },
    {
      id: "action",
      header: t("columns.action"),
      cell: (row) => (
        <Badge variant="outline" className="font-mono text-xs">
          {row.action}
        </Badge>
      ),
    },
    {
      id: "target",
      header: t("columns.target"),
      cellClassName: "text-sm",
      cell: (row) =>
        row.targetKind ? (
          <div className="font-mono text-xs">
            {row.targetKind}
            {row.targetSlug ? `:${row.targetSlug}` : ""}
            {row.targetId ? ` (${row.targetId})` : ""}
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
          <Badge className={style.cls + " gap-1 px-2 py-0.5 text-xs"} variant="secondary">
            {style.icon}
            {row.decision}
          </Badge>
        );
      },
    },
  ];

  return (
    <PageShell
      title={t("title")}
      description={description}
      actions={
        <div className="flex items-center gap-2">
          <RetentionDialog
            currentDays={retentionDays}
            canSave={canEditRetention}
            saving={savingRetention}
            onSave={saveRetention}
          />
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
      <DataTable
        label="Audit events"
        controller={table}
        columns={columns}
        getRowId={(row) => row.id}
        // Rows open the detail sheet; they are not links, so they must
        // not pretend to be.
        onRowActivate={setActiveRow}
        // Action and timestamp: what distinguishes one audit row from the
        // next, and it carries the first cell's visible text.
        rowLabel={(row) => `${row.action} ${fmt.formatDateTime(row.occurredAt)}`}
        // DataTable owns the search box, so the field names itself in
        // its own placeholder: it is the action filter, not a free-text
        // search across the row.
        searchPlaceholder={`${t("filters.actionLabel")}: ${t("filters.actionPlaceholder")}`}
        toolbar={
          <>
            <div className="flex items-center gap-1.5">
              <Label htmlFor="audit-decision-filter" className="text-muted-foreground text-xs">
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
            <div className="flex items-center gap-1.5">
              <Label htmlFor="audit-from-date" className="text-muted-foreground text-xs">
                {t("filters.fromLabel")}
              </Label>
              <Input
                id="audit-from-date"
                type="date"
                value={fromDate}
                onChange={(e) => setFromDate(e.target.value)}
                max={maxDate}
                className="w-40"
              />
            </div>
            <div className="flex items-center gap-1.5">
              <Label htmlFor="audit-to-date" className="text-muted-foreground text-xs">
                {t("filters.toLabel")}
              </Label>
              <Input
                id="audit-to-date"
                type="date"
                value={toDate}
                onChange={(e) => setToDate(e.target.value)}
                max={maxDate}
                className="w-40"
              />
            </div>
          </>
        }
        empty={{
          icon: <ScrollTextIcon className="size-5" />,
          title: t("emptyTitle"),
          description: t("emptyDescription"),
        }}
        emptyFiltered={{
          title: "No events for that action",
          description:
            "The action filter matches exactly: “team.create”, not “team”. Check the full action name on a row you can see, or clear the filter to get the whole range back.",
        }}
      />

      <AuditDetailsSheet row={activeRow} onOpenChange={(open) => !open && setActiveRow(null)} />
    </PageShell>
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
