"use client";

import {
  AlertTriangleIcon,
  CalendarClockIcon,
  GitBranchIcon,
  GitPullRequestIcon,
  TrashIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Can } from "@/components/Can";
import { previewHasAvailableBinding } from "@/components/screens/previews/preview-binding";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { CopyBadge } from "@/components/CopyBadge";
import { type Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import type {
  AstroliftPreviewEnvironment,
  PreviewStatus,
} from "@/graphql/lifecycle/lifecycle.types";
import { cn } from "@/lib/utils";
import { useFormatters } from "@/lib/i18n/formatters";

import { isStale, STALE_DAYS, type useAppPreviews } from "./use-app-previews";

export type AppPreviewsScreenProps = ReturnType<typeof useAppPreviews> & {
  /** Where "Enable previews" sends the operator: this app's config tab. */
  configHref: string;
};

const statusToDot: Record<PreviewStatus, "ok" | "warn" | "error" | "muted" | "pending"> = {
  building: "pending",
  running: "ok",
  failed: "error",
  torn_down: "muted",
};

// Live countdown. Returns "expired" when ttl_until is in the past, an
// "Xd Yh" / "Xh Ym" / "Xm" string otherwise. Granularity drops as the
// window shrinks so the column doesn't show "0d 5h 23m 14s" on a
// preview about to be torn down.
function formatCountdown(
  ttlIsoString: string,
  now: number,
  t: ReturnType<typeof useTranslations<"apps.previews">>
): { label: string; expired: boolean } {
  const target = new Date(ttlIsoString).getTime();
  if (!Number.isFinite(target)) return { label: "—", expired: false };
  const delta = target - now;
  if (delta <= 0) return { label: t("countdown.expired"), expired: true };
  const seconds = Math.floor(delta / 1000);
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  if (days >= 1) return { label: t("countdown.daysHours", { days, hours }), expired: false };
  if (hours >= 1)
    return { label: t("countdown.hoursMinutes", { hours, minutes: mins }), expired: false };
  return { label: t("countdown.minutes", { minutes: mins }), expired: false };
}

// Render bytes in Mi / Gi, mirroring kubectl describe so the column
// aligns with what operators read in pod specs. The backend returns
// raw bytes for currency-of-rounding reasons.
function formatMemoryBytes(byteCount: number): string {
  if (byteCount <= 0) return "0 Mi";
  const gi = 1024 ** 3;
  const mi = 1024 ** 2;
  if (byteCount >= gi) {
    const value = byteCount / gi;
    return value >= 100 ? `${Math.round(value)} Gi` : `${value.toFixed(1)} Gi`;
  }
  const value = byteCount / mi;
  return value >= 100 ? `${Math.round(value)} Mi` : `${value.toFixed(0)} Mi`;
}

function formatCpuCores(cores: number): string {
  if (cores <= 0) return "0 CPU";
  if (cores < 1) return `${Math.round(cores * 1000)}m CPU`;
  return `${Number(cores.toFixed(2))} CPU`;
}

// Docs URL — keeps the empty-state "Learn more" link consistent across
// the platform. Points at the in-app documentation hub (#894) so operators
// stay inside the app.
const DOCS_PREVIEWS_HREF = "/documentation";

function CreatePreviewSheet({
  disabled,
  creating: loading,
  onCreate,
}: {
  disabled: boolean;
  creating: boolean;
  onCreate: (branch: string) => Promise<boolean>;
}) {
  const t = useTranslations("apps.previews");
  const [open, setOpen] = React.useState(false);
  const [branch, setBranch] = React.useState("");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const b = branch.trim();
    if (!b) return;
    if (await onCreate(b)) {
      setOpen(false);
      setBranch("");
    }
  }

  return (
    <>
      <Can permission="app.deploy">
        <Button size="sm" onClick={() => setOpen(true)} disabled={disabled}>
          <GitBranchIcon className="size-3" /> {t("create.action")}
        </Button>
      </Can>
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent className="flex flex-col">
          <SheetHeader>
            <SheetTitle>{t("create.title")}</SheetTitle>
            <SheetDescription>{t("create.description")}</SheetDescription>
          </SheetHeader>
          <form onSubmit={handleSubmit} className="flex flex-1 flex-col gap-4 px-4 pb-4">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="preview-branch">{t("create.branch")}</Label>
              <Input
                id="preview-branch"
                placeholder="feature/my-branch"
                value={branch}
                onChange={(e) => setBranch(e.target.value)}
                autoFocus
              />
              <p className="text-muted-foreground text-xs">{t("create.hint")}</p>
            </div>
            <SheetFooter>
              <Button type="button" variant="outline" onClick={() => setOpen(false)}>
                {t("create.cancel")}
              </Button>
              <Button type="submit" disabled={loading || !branch.trim()}>
                {loading ? t("create.creating") : t("create.action")}
              </Button>
            </SheetFooter>
          </form>
        </SheetContent>
      </Sheet>
    </>
  );
}

/**
 * The Previews view of an app's Deployments tab (spec 44 §4.4, §5.1): the
 * per-PR (and manual) preview environments on the embedded list, sharing
 * the deployments list's view picker, with their TTL countdowns, footprint
 * and spend. Extend and tear down sit in each row's `⋯`; Create preview
 * leads the toolbar. Pure.
 */
export function AppPreviewsScreen({
  app: a,
  list,
  rows,
  newRows,
  pageLoading,
  pageError,
  onRetry,
  nextCursor,
  totalCount,
  previewCount,
  counts,
  spend,
  stalePreviews,
  canDeploy,
  tearingDown,
  extending,
  creating,
  onExtend: handleExtend,
  onTearDown: handleTearDown,
  onCreate,
  configHref,
}: AppPreviewsScreenProps) {
  const t = useTranslations("apps.previews");
  const fmt = useFormatters();
  const viewLabels: Record<string, string> = {
    all: t("list.views.all"),
    mine: t("list.views.mine"),
    waiting: t("list.views.waiting"),
    failed: t("list.views.failed"),
    today: t("list.views.today"),
    previews: t("list.views.previews"),
  };
  const localizedList = {
    ...list,
    definition: {
      ...list.definition,
      searchPlaceholder: t("list.search"),
      views: list.definition.views?.map((view) => ({
        ...view,
        label: viewLabels[view.key] ?? view.label,
      })),
    },
  };

  const [tearDownTarget, setTearDownTarget] = React.useState<AstroliftPreviewEnvironment | null>(
    null
  );

  // Live countdown ticker. Re-renders every 60s: that's the right cadence
  // for "5d 3h" granularity; faster updates would flash the column without
  // changing anything operators care about.
  const [now, setNow] = React.useState<number>(() => Date.now());
  React.useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 60_000);
    return () => clearInterval(id);
  }, []);

  const enabled = a?.previewEnabled ?? true;

  const columns: Column<AstroliftPreviewEnvironment>[] = [
    {
      id: "pr",
      header: t("columns.pr"),
      cellClassName: "max-w-72",
      cell: (p) => (
        <div className="flex min-w-0 items-start gap-2">
          <StatusDot status={statusToDot[p.status]} className="mt-1.5 shrink-0" />
          <div className="flex min-w-0 flex-col">
            <span className="flex min-w-0 items-center gap-1">
              {p.prNumber > 0 ? (
                p.prUrl ? (
                  <a
                    href={p.prUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="shrink-0 font-mono font-medium hover:underline"
                    title={t("openPr", { pr: p.prNumber })}
                  >
                    #{p.prNumber}
                  </a>
                ) : (
                  <span className="shrink-0 font-mono font-medium">#{p.prNumber}</span>
                )
              ) : null}
              <span
                className={cn(
                  "truncate font-mono text-xs",
                  p.prNumber > 0 ? "text-muted-foreground" : "font-medium"
                )}
                title={p.branch}
              >
                {p.branch}
              </span>
              {p.isManual && (
                <Badge variant="outline" className="text-2xs shrink-0 uppercase">
                  {t("manual")}
                </Badge>
              )}
            </span>
            <span className="text-muted-foreground truncate font-mono text-xs" title={p.namespace}>
              ns {p.namespace}
              {p.commitSha && <> · {p.commitSha.slice(0, 7)}</>}
            </span>
          </div>
        </div>
      ),
    },
    {
      id: "hostname",
      header: t("columns.hostname"),
      cellClassName: "relative z-10 max-w-72",
      cell: (p) =>
        p.status === "running" && previewHasAvailableBinding(p) ? (
          <CopyBadge
            value={p.hostname}
            openHref={`https://${p.hostname}`}
            openLabel={t("openInTab")}
            title={t("copyHostname")}
          />
        ) : (
          <span
            className="text-muted-foreground block truncate font-mono text-xs"
            title={p.hostname}
          >
            {p.hostname}
          </span>
        ),
    },
    {
      id: "ttl",
      header: t("columns.ttl"),
      cellClassName: "relative z-10",
      cell: (p) => {
        if (p.status === "torn_down") {
          return <span className="text-muted-foreground text-xs">—</span>;
        }
        const countdown = formatCountdown(p.ttlUntil, now, t);
        return (
          <Tooltip>
            <TooltipTrigger asChild>
              <span
                className={cn(
                  "font-mono text-xs",
                  countdown.expired ? "text-danger-fg" : "text-muted-foreground"
                )}
              >
                {countdown.label}
              </span>
            </TooltipTrigger>
            <TooltipContent>
              {t("ttlTooltip", {
                date: Number.isFinite(new Date(p.ttlUntil).getTime())
                  ? fmt.formatDateTime(p.ttlUntil)
                  : "—",
              })}
            </TooltipContent>
          </Tooltip>
        );
      },
    },
    {
      id: "footprint",
      header: t("columns.footprint"),
      cellClassName: "relative z-10",
      cell: (p) => {
        const aggregate = p.aggregateResources;
        if (aggregate.podCount === 0) {
          return <span className="text-muted-foreground text-xs">—</span>;
        }
        const dailyCost = p.estimatedDailyCostUsd;
        return (
          <Tooltip>
            <TooltipTrigger asChild>
              <div className="cursor-default font-mono">
                <div className="text-xs">
                  {formatCpuCores(aggregate.cpuCores)} · {formatMemoryBytes(aggregate.memoryBytes)}
                </div>
                <div className="text-muted-foreground text-xs">
                  {dailyCost != null
                    ? t("costPerDay", { cost: fmt.formatCurrency(dailyCost) })
                    : t("costUnavailable")}
                </div>
              </div>
            </TooltipTrigger>
            <TooltipContent>
              {dailyCost != null
                ? t("costTooltip", {
                    pods: aggregate.podCount,
                    cost: fmt.formatCurrency(dailyCost),
                  })
                : t("costTooltipUnavailable", { pods: aggregate.podCount })}
              {/* The driver's own qualifications on its own number. */}
              {p.estimatedCostNotes.map((note) => (
                <span key={note} className="mt-1 block max-w-xs text-xs opacity-80">
                  {note}
                </span>
              ))}
            </TooltipContent>
          </Tooltip>
        );
      },
    },
    {
      id: "lastDeploy",
      header: t("columns.lastDeploy"),
      cellClassName: "text-muted-foreground font-mono text-xs whitespace-nowrap",
      cell: (p) => (
        <>
          {p.lastDeployedAt ? fmt.formatDateTime(p.lastDeployedAt) : "—"}
          {isStale(p) && (
            <Badge variant="outline" className="text-2xs ml-2 uppercase">
              {t("staleBadge")}
            </Badge>
          )}
        </>
      ),
    },
    {
      id: "status",
      header: t("columns.status"),
      cellClassName: "font-mono text-xs",
      cell: (p) => <span title={p.status}>{t(`status.${p.status}`)}</span>,
    },
  ];

  const rowActions = canDeploy
    ? (p: AstroliftPreviewEnvironment) =>
        p.status === "torn_down" ? (
          <DropdownMenuItem disabled>{t("tornDownAction")}</DropdownMenuItem>
        ) : (
          <>
            <DropdownMenuItem disabled={extending} onSelect={() => void handleExtend(p, 1)}>
              <CalendarClockIcon className="size-4" />
              {t("extendAction", { duration: t("extend.1d") })}
            </DropdownMenuItem>
            <DropdownMenuItem disabled={extending} onSelect={() => void handleExtend(p, 7)}>
              <CalendarClockIcon className="size-4" />
              {t("extendAction", { duration: t("extend.7d") })}
            </DropdownMenuItem>
            <DropdownMenuItem
              variant="destructive"
              disabled={tearingDown}
              onSelect={() => setTearDownTarget(p)}
            >
              <TrashIcon className="size-4" />
              {t("tearDown")}
            </DropdownMenuItem>
          </>
        )
    : undefined;

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-4">
      {a && !enabled && (
        <Notice
          icon={<AlertTriangleIcon className="size-4" />}
          title={t("disabled.title")}
          description={t("disabled.description")}
        />
      )}

      {stalePreviews.length > 0 && (
        <Notice
          icon={<CalendarClockIcon className="size-4" />}
          title={t("stale.title", { count: stalePreviews.length })}
          description={t("stale.description", { days: STALE_DAYS })}
        />
      )}

      <div className="flex min-w-0 flex-wrap items-center gap-x-6 gap-y-2">
        <span className="text-muted-foreground font-mono text-xs">
          {t("groupSummary", {
            running: counts.running,
            failed: counts.failed,
            tornDown: counts.tornDown,
          })}
        </span>
        {/* #660: preview spend roll-up, only when at least one live preview is priced. */}
        {spend.liveCount > 0 && (spend.priced > 0 || spend.unpriced > 0) && (
          <SpendSummary spend={spend} />
        )}
        <div className="ml-auto">
          <CreatePreviewSheet disabled={!enabled} creating={creating} onCreate={onCreate} />
        </div>
      </div>

      <TooltipProvider delayDuration={200}>
        <ListPage<AstroliftPreviewEnvironment>
          embedded
          list={localizedList}
          label={t("list.label")}
          columns={columns}
          rows={rows}
          getRowId={(p) => p.id}
          rowActions={rowActions}
          loading={pageLoading || !a}
          error={pageError}
          onRetry={onRetry}
          nextCursor={nextCursor}
          totalCount={totalCount}
          newRows={newRows}
          empty={{
            icon: <GitPullRequestIcon className="size-5" />,
            title: t("empty.title"),
            description: enabled ? t("empty.enabledHint") : t("empty.disabledHint"),
            actionHref: enabled ? (a?.sourceUrl ?? undefined) : configHref,
            actionLabel: enabled ? (a?.sourceUrl ? t("openRepo") : undefined) : t("enablePreviews"),
            learnMoreHref: DOCS_PREVIEWS_HREF,
            learnMoreLabel: t("empty.learnMore"),
          }}
        />
      </TooltipProvider>

      {/* The onboarding steps follow the list while it is genuinely empty. */}
      {a && !pageLoading && !pageError && previewCount === 0 && rows.length === 0 && (
        <EmptyStateSteps />
      )}

      {a && previewCount > 0 && (
        <p className="text-muted-foreground text-center text-xs">
          {t("footer", {
            count: a.previewMaxActive,
            days: STALE_DAYS,
          })}
        </p>
      )}

      <ConfirmDialog
        open={tearDownTarget !== null}
        onOpenChange={(next) => {
          if (!next) setTearDownTarget(null);
        }}
        title={
          tearDownTarget ? t("confirmTitle", { pr: tearDownTarget.prNumber }) : t("confirmFallback")
        }
        description={
          tearDownTarget
            ? t("confirmDescription", {
                namespace: tearDownTarget.namespace,
                hostname: tearDownTarget.hostname,
                branch: tearDownTarget.branch,
              })
            : t("confirmDescriptionFallback")
        }
        confirmLabel={t("tearDown")}
        destructive
        onConfirm={async () => {
          if (tearDownTarget) await handleTearDown(tearDownTarget);
        }}
      />
    </div>
  );
}

/** A warning strip above the list: previews off, or stale previews to sweep. */
function Notice({
  icon,
  title,
  description,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
}) {
  return (
    <div
      role="status"
      className="border-warning-border bg-warning/5 flex min-w-0 items-start gap-3 rounded-md border px-4 py-3"
    >
      <span className="text-warning-fg mt-0.5 shrink-0" aria-hidden>
        {icon}
      </span>
      <div className="min-w-0">
        <p className="text-sm font-medium [overflow-wrap:anywhere]">{title}</p>
        <p className="text-muted-foreground text-xs [overflow-wrap:anywhere]">{description}</p>
      </div>
    </div>
  );
}

/** Daily spend, the month's projection, and how much of it is priced. */
function SpendSummary({ spend }: { spend: AppPreviewsScreenProps["spend"] }) {
  const t = useTranslations("apps.previews");
  const fmt = useFormatters();
  return (
    <div className="flex min-w-0 flex-wrap items-baseline gap-x-4 gap-y-1 text-xs">
      <span>
        <span className="text-muted-foreground">{t("spend.daily")} </span>
        <span className="font-mono">
          {spend.priced > 0 ? fmt.formatCurrency(spend.dailyTotal) : "—"}
        </span>
      </span>
      <span className="inline-flex items-center gap-1.5">
        <span className="text-muted-foreground">{t("spend.monthly")} </span>
        <span className="font-mono">
          {spend.priced > 0 ? fmt.formatCurrency(spend.monthlyProjection) : "—"}
        </span>
        {spend.approximate > 0 && (
          <Badge variant="outline" className="text-2xs gap-1 uppercase">
            <AlertTriangleIcon className="size-3" />
            {t("spend.approximate")}
          </Badge>
        )}
      </span>
      <span className="text-muted-foreground min-w-0 [overflow-wrap:anywhere]">
        <span className="font-mono">
          {t("spend.priced", { priced: spend.priced, total: spend.liveCount })}
        </span>
        {spend.unpriced > 0 && <> · {t("spend.unpriced", { count: spend.unpriced })}</>}
        {spend.approximate > 0 && <> · {t("spend.overcount", { count: spend.approximate })}</>}
      </span>
    </div>
  );
}

// ---- empty-state onboarding ------------------------------------------

function EmptyStateSteps() {
  const t = useTranslations("apps.previews");
  return (
    <ol className="text-muted-foreground mx-auto mt-4 max-w-md list-decimal space-y-1.5 pl-5 text-left text-sm">
      <li>{t("empty.step1")}</li>
      <li>{t("empty.step2")}</li>
      <li>{t("empty.step3")}</li>
    </ol>
  );
}
