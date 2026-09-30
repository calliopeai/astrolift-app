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

import { Feed } from "@/components/feed/Feed";
import { FilterBar } from "@/components/list/FilterBar";
import type { ListStateController } from "@/components/list/list-state";
import { ShellHeader } from "@/components/shell/ShellHeader";
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

export interface AuditEventsFeed {
  items: AstroliftAuditEvent[];
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
  /** Events that arrived above the ones being read, behind the "new" pill. */
  newCount: number;
  onShowNew: () => void;
}

export interface AuditLogScreenProps {
  /** List state: views, search and filter chips (see audit-list.ts). */
  list: ListStateController;
  /** The events as a feed: newest first, older ones on the server's cursor. */
  events: AuditEventsFeed;
  totalCount: number | null;
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
 * Admin › Usage & governance › Audit: the audit trail as a Feed (list rule
 * 5: it only grows), under the list's own views and filter bar (rule 4).
 * Views All · Mine · Denied as the header's tabs; chips for actor, action,
 * target kind, decision and since; newest first, grouped by day, older
 * events loading on the server's cursor as the reader nears the end, new
 * ones behind the pill. A line opens the detail sheet; export and retention
 * stay as they were. Pure; the data half is useAuditLog.
 */
export function AuditLogScreen({
  list,
  events,
  totalCount,
  retentionDays,
  exporting,
  onExport: handleExport,
  canEditRetention,
  savingRetention,
  saveRetention,
}: AuditLogScreenProps) {
  const t = useTranslations("lists.audit");

  const [activeRow, setActiveRow] = React.useState<AstroliftAuditEvent | null>(null);

  const { definition: def, state } = list;
  const view = def.views.find((v) => v.key === state.view);

  const exportMenu = (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" aria-label={t("export.button")} disabled={exporting}>
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
  );

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-4 p-6">
      <ShellHeader
        crumbs={adminCrumbs("audit", t("title"))}
        title={t("title")}
        context={
          <span className="font-mono">
            {totalCount != null ? t("eventsCount", { count: totalCount }) : null}
            {totalCount != null && retentionDays != null ? " · " : null}
            {retentionDays != null ? t("retainedDays", { days: retentionDays }) : null}
          </span>
        }
        primaryAction={
          <RetentionDialog
            currentDays={retentionDays}
            canSave={canEditRetention}
            saving={savingRetention}
            onSave={saveRetention}
          />
        }
        tabs={def.views.map((v) => ({
          key: v.key,
          label: v.label,
          href: list.viewHref(v.key),
          active: v.key === state.view,
        }))}
        tabsAriaLabel={t("viewsLabel")}
      />
      {view?.note && (
        <p className="text-muted-foreground -mt-2 min-w-0 text-xs [overflow-wrap:anywhere]">
          {view.note}
        </p>
      )}
      <FilterBar list={list} columns={[]} cards={false} menu={exportMenu} />

      <Feed<AstroliftAuditEvent>
        label={t("feedLabel")}
        {...events}
        keyOf={(row) => row.id}
        groupBy={{ day: (row) => row.occurredAt }}
        maxHeight="max-h-160"
        dense
        empty={
          list.isFiltered
            ? {
                icon: <ScrollTextIcon className="size-5" />,
                title: t("filteredTitle"),
                description: t("filteredDescription"),
              }
            : {
                icon: <ScrollTextIcon className="size-5" />,
                title: t("emptyTitle"),
                description: t("emptyDescription"),
              }
        }
        renderItem={(row) => <AuditLine row={row} onOpen={() => setActiveRow(row)} />}
      />

      <AuditDetailsSheet row={activeRow} onOpenChange={(open) => !open && setActiveRow(null)} />
    </div>
  );
}

/**
 * One audit event: when, who, what, on which target, and the decision. The
 * whole line is one button, named by action and time (what tells one event
 * from the next), that opens the detail sheet.
 */
function AuditLine({ row, onOpen }: { row: AstroliftAuditEvent; onOpen: () => void }) {
  const fmt = useFormatters();
  const t = useTranslations("lists.audit");
  const style = decisionStyles[row.decision] ?? decisionStyles.UNKNOWN;
  return (
    <button
      type="button"
      aria-label={`${row.action} ${fmt.formatDateTime(row.occurredAt)}`}
      onClick={onOpen}
      className="hover:bg-muted/50 focus-visible:ring-ring flex w-full min-w-0 flex-wrap items-center gap-x-3 gap-y-1 rounded-sm px-1 py-0.5 text-left focus-visible:ring-2 focus-visible:outline-none"
    >
      <time dateTime={row.occurredAt} className="text-muted-foreground shrink-0 font-mono text-xs">
        {fmt.formatDateTime(row.occurredAt)}
      </time>
      <Badge
        variant="outline"
        className="max-w-72 min-w-0 truncate font-mono text-xs"
        title={row.action}
      >
        {row.action}
      </Badge>
      <span className="max-w-64 min-w-0 truncate text-sm" title={row.actorDisplay || row.actorKind}>
        {row.actorDisplay || row.actorKind}
      </span>
      {row.targetKind ? (
        <span
          className="text-muted-foreground min-w-0 flex-1 truncate font-mono text-xs"
          title={targetText(row)}
        >
          {targetText(row)}
        </span>
      ) : (
        <span className="flex-1" />
      )}
      <Badge
        className={style.cls + " shrink-0 gap-1 px-2 py-0.5 font-mono text-xs"}
        variant="secondary"
      >
        {style.icon}
        {t.has(`decisions.${row.decision}`) ? t(`decisions.${row.decision}`) : row.decision}
      </Badge>
    </button>
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
                {t.has(`decisions.${row.decision}`) ? t(`decisions.${row.decision}`) : row.decision}
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
