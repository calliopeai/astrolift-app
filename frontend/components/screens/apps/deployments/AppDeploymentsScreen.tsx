"use client";

import { AlertTriangleIcon, BoxIcon, ClockIcon, GitCompareIcon, XIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";

import { DeploymentStatusPill } from "@/components/DeploymentStatusPill";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type {
  AstroliftAppEnvironment,
  AstroliftDeployment,
} from "@/graphql/lifecycle/lifecycle.types";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";
import { cn } from "@/lib/utils";

import {
  formatDuration,
  formatTime,
  IN_FLIGHT,
  STATUS_BUCKET_KEYS,
  type StatusBucket,
  statusMatches,
} from "./app-deployments-format";

// Number of visible columns in the deployments table. The inline-expand
// row uses this for its `colSpan` so the panel always spans the width
// of the table regardless of how many columns we render.
const TABLE_COLUMNS = 7;

const DEPLOYMENTS_PAGE_SIZE = 20;

export interface CompareSlotArgs {
  open: boolean;
  onOpenChange: (next: boolean) => void;
  deployA: AstroliftDeployment;
  deployB: AstroliftDeployment;
}

export interface AppDeploymentsScreenProps {
  slug: string;
  app: Pick<AstroliftRegisteredApp, "name" | "slug"> | null;
  /** First load of the app only. */
  loading: boolean;
  deployments: AstroliftDeployment[];
  deploymentsLoading: boolean;
  environments: Pick<AstroliftAppEnvironment, "name">[];
  statusBucket: StatusBucket;
  setStatusBucket: (bucket: StatusBucket) => void;
  envFilter: string;
  setEnvFilter: (env: string) => void;
  search: string;
  setSearch: (search: string) => void;
  openId: string | null;
  setOpenId: (id: string | null) => void;
  toggleOpen: (id: string) => void;
  /** Link to this app's environments page (empty-state CTA and footer). */
  environmentsHref: string;
  /** The app tab row. */
  tabs?: React.ReactNode;
  /** A row's lifecycle actions; each carries its own mutations. */
  renderRowActions?: (deployment: AstroliftDeployment) => React.ReactNode;
  /** The opened row's detail, mounted only while it is open. */
  renderExpandPanel?: (deployment: AstroliftDeployment, onClose: () => void) => React.ReactNode;
  /** The compare sheet for exactly two selected deployments. */
  renderCompare?: (args: CompareSlotArgs) => React.ReactNode;
}

/** An app's deployment history: stats, filters, compare and the expandable table. */
export function AppDeploymentsScreen({
  slug,
  app: a,
  loading,
  deployments: allDeployments,
  deploymentsLoading,
  environments: envList,
  statusBucket,
  setStatusBucket,
  envFilter,
  setEnvFilter,
  search,
  setSearch,
  openId,
  setOpenId,
  toggleOpen,
  environmentsHref,
  tabs,
  renderRowActions,
  renderExpandPanel,
  renderCompare,
}: AppDeploymentsScreenProps) {
  const tCommon = useTranslations("apps.common");
  const t = useTranslations("apps.deployments");

  // Client-side pagination over the filtered list. Reset back to page 1
  // whenever the active filter set changes so a narrowed view always
  // starts at the top instead of stranding the user on an empty page.
  const [page, setPage] = React.useState(1);
  React.useEffect(() => {
    setPage(1);
  }, [statusBucket, envFilter, search]);

  // #652 — multi-select for deploy-vs-deploy compare. Exactly two selected
  // enables the Compare action; anything else disables it.
  const [selectedIds, setSelectedIds] = React.useState<Set<string>>(new Set());
  const [compareOpen, setCompareOpen] = React.useState(false);

  const filtered = React.useMemo(() => {
    return allDeployments.filter((d) => {
      if (!statusMatches(statusBucket, d.status)) return false;
      if (envFilter !== "all" && d.environmentName !== envFilter) return false;
      if (search) {
        const needle = search.toLowerCase();
        const hay = [
          d.imageTag ?? "",
          d.commitSha ?? "",
          d.branch ?? "",
          d.environmentName,
          d.workloadSlug ?? "",
        ]
          .join(" ")
          .toLowerCase();
        if (!hay.includes(needle)) return false;
      }
      return true;
    });
  }, [allDeployments, statusBucket, envFilter, search]);

  const visibleDeployments = React.useMemo(
    () => filtered.slice(0, page * DEPLOYMENTS_PAGE_SIZE),
    [filtered, page]
  );

  const toggleSelect = React.useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const visibleIds = React.useMemo(() => filtered.map((d) => d.id), [filtered]);
  const allVisibleSelected = visibleIds.length > 0 && visibleIds.every((id) => selectedIds.has(id));
  const someVisibleSelected = !allVisibleSelected && visibleIds.some((id) => selectedIds.has(id));

  const toggleSelectAll = React.useCallback(() => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      const allChecked = visibleIds.every((id) => next.has(id));
      if (allChecked) {
        for (const id of visibleIds) next.delete(id);
      } else {
        for (const id of visibleIds) next.add(id);
      }
      return next;
    });
  }, [visibleIds]);

  const selectedList = React.useMemo(
    () => allDeployments.filter((d) => selectedIds.has(d.id)),
    [allDeployments, selectedIds]
  );
  const canCompare = selectedList.length === 2;

  if (loading && !a) {
    return (
      <PageShell title={tCommon("loading")}>
        <Skeleton className="h-32 w-full" />
      </PageShell>
    );
  }

  if (!a) {
    return (
      <PageShell title={tCommon("notFound")}>
        <EmptyState
          icon={<AlertTriangleIcon className="size-5" />}
          title={tCommon("notFoundSlug", { slug })}
          description={tCommon("notFoundDescription")}
          actionHref="/apps"
          actionLabel={tCommon("backToApps")}
        />
      </PageShell>
    );
  }

  return (
    <PageShell
      title={t("title", { name: a.name })}
      description={
        <span className="text-muted-foreground font-mono text-xs">
          {t("description", { slug: a.slug })}
        </span>
      }
    >
      {tabs}

      {allDeployments.length > 0 && !deploymentsLoading && (
        <DeploymentStatsBar deployments={allDeployments} />
      )}

      {/* ─── filter bar ─────────────────────────────────────────────── */}
      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-1">
          {STATUS_BUCKET_KEYS.map((o) => {
            const count =
              o.value === "all"
                ? allDeployments.length
                : allDeployments.filter((d) => statusMatches(o.value, d.status)).length;
            return (
              <button
                key={o.value}
                type="button"
                onClick={() => setStatusBucket(o.value)}
                className={cn(
                  "rounded-full border px-3 py-0.5 text-xs font-medium transition-colors",
                  statusBucket === o.value
                    ? "border-foreground/30 bg-foreground text-background"
                    : "text-muted-foreground hover:border-border hover:text-foreground border-transparent"
                )}
              >
                {t(`statusBuckets.${o.key}`)}
                {count > 0 && (
                  <span
                    className={cn(
                      "ml-1.5 tabular-nums",
                      statusBucket === o.value ? "opacity-70" : "opacity-60"
                    )}
                  >
                    {count}
                  </span>
                )}
              </button>
            );
          })}
          <span className="text-muted-foreground ml-auto text-xs">
            {t("filters.counts", { filtered: filtered.length, total: allDeployments.length })}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Input
            placeholder={t("filters.search")}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="h-7 max-w-48 text-xs"
          />
          {envList.length > 1 && (
            <div className="flex items-center gap-1">
              {["all", ...envList.map((e) => e.name)].map((env) => (
                <button
                  key={env}
                  type="button"
                  onClick={() => setEnvFilter(env)}
                  className={cn(
                    "rounded px-2 py-0.5 text-xs transition-colors",
                    envFilter === env
                      ? "bg-accent text-foreground"
                      : "text-muted-foreground hover:text-foreground"
                  )}
                >
                  {env === "all" ? t("filters.anyEnv") : env}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* #652 — compare toolbar. Surfaces selection count + Compare CTA
          whenever at least one row is selected. Compare requires exactly
          two so the diff has a clear A→B direction. */}
      {selectedList.length > 0 && (
        <div className="bg-accent/30 border-border flex flex-wrap items-center gap-3 rounded-md border px-3 py-2 text-xs">
          <span className="font-medium">{selectedList.length} selected</span>
          <Button
            size="sm"
            disabled={!canCompare}
            onClick={() => setCompareOpen(true)}
            className="ml-auto"
          >
            <GitCompareIcon className="size-3.5" />
            Compare {canCompare ? "" : "(select exactly 2)"}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setSelectedIds(new Set())}>
            <XIcon className="size-3.5" />
            Clear
          </Button>
        </div>
      )}

      <Card>
        <CardContent className="p-0">
          {deploymentsLoading && allDeployments.length === 0 ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : filtered.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<BoxIcon className="size-5" />}
                title={t("emptyTitle")}
                description={allDeployments.length === 0 ? t("emptyNever") : t("emptyTryAgain")}
                actionHref={environmentsHref}
                actionLabel={allDeployments.length === 0 ? t("emptyStart") : undefined}
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-8">
                    <input
                      type="checkbox"
                      aria-label="Select all visible deployments"
                      checked={allVisibleSelected}
                      ref={(el) => {
                        if (el) el.indeterminate = someVisibleSelected;
                      }}
                      onChange={toggleSelectAll}
                    />
                  </TableHead>
                  <TableHead>{t("table.status")}</TableHead>
                  <TableHead>{t("table.imageCommit")}</TableHead>
                  <TableHead>{t("table.env")}</TableHead>
                  <TableHead>{t("table.when")}</TableHead>
                  <TableHead>{t("table.duration")}</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {visibleDeployments.flatMap((d) => {
                  const isOpen = openId === d.id;
                  return [
                    <DeploymentRow
                      key={d.id}
                      deployment={d}
                      isOpen={isOpen}
                      selected={selectedIds.has(d.id)}
                      onToggleSelect={() => toggleSelect(d.id)}
                      onToggleOpen={() => toggleOpen(d.id)}
                      actions={renderRowActions?.(d)}
                    />,
                    isOpen ? (
                      <TableRow key={`${d.id}-panel`} className="bg-muted/20 hover:bg-muted/20">
                        <TableCell colSpan={TABLE_COLUMNS} className="p-0">
                          {renderExpandPanel?.(d, () => setOpenId(null))}
                        </TableCell>
                      </TableRow>
                    ) : null,
                  ];
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {visibleDeployments.length < filtered.length && (
        <div className="flex justify-center">
          <Button variant="outline" size="sm" onClick={() => setPage((p) => p + 1)}>
            Show {Math.min(DEPLOYMENTS_PAGE_SIZE, filtered.length - visibleDeployments.length)} more
            <span className="text-muted-foreground ml-1">
              ({visibleDeployments.length} of {filtered.length})
            </span>
          </Button>
        </div>
      )}

      <p className="text-muted-foreground text-center text-xs">
        {t("footer", { count: allDeployments.length })}{" "}
        <Link href={environmentsHref} className="hover:text-foreground underline">
          {t("rollNew")}
        </Link>
        .
      </p>

      {canCompare &&
        renderCompare?.({
          open: compareOpen,
          onOpenChange: setCompareOpen,
          deployA: selectedList[0],
          deployB: selectedList[1],
        })}
    </PageShell>
  );
}

// ─── Row ───────────────────────────────────────────────────────────────

export interface DeploymentRowProps {
  deployment: AstroliftDeployment;
  isOpen: boolean;
  selected: boolean;
  onToggleSelect: () => void;
  onToggleOpen: () => void;
  /** The row's action buttons. */
  actions?: React.ReactNode;
}

export function DeploymentRow({
  deployment,
  isOpen,
  selected,
  onToggleSelect,
  onToggleOpen,
  actions,
}: DeploymentRowProps) {
  const d = deployment;
  return (
    <TableRow
      className={cn(
        "hover:bg-accent/30 cursor-pointer",
        isOpen && "bg-accent/40 hover:bg-accent/40"
      )}
      onClick={onToggleOpen}
      aria-expanded={isOpen}
    >
      <TableCell className="w-8" onClick={(e) => e.stopPropagation()}>
        <input
          type="checkbox"
          aria-label={`Select ${d.imageTag ?? d.id}`}
          checked={selected}
          onChange={onToggleSelect}
        />
      </TableCell>
      <TableCell className="max-w-64">
        <DeploymentStatusPill status={d.status} />
        {d.statusReason && (
          // Why it failed or what it waits on, without opening the row (#2123).
          <p
            className="text-muted-foreground text-2xs mt-1 line-clamp-2 [overflow-wrap:anywhere]"
            title={d.statusReason}
          >
            {d.statusReason}
          </p>
        )}
      </TableCell>
      <TableCell>
        <div className="font-mono text-xs">{d.imageTag || "—"}</div>
        {d.workloadSlug && (
          <div className="text-muted-foreground text-2xs mt-0.5 font-mono">{d.workloadSlug}</div>
        )}
      </TableCell>
      <TableCell>
        <Badge variant="outline" className="font-mono text-xs">
          {d.environmentName}
        </Badge>
      </TableCell>
      <TableCell className="text-sm whitespace-nowrap">
        {formatTime(d.startedAt ?? d.createdAt)}
      </TableCell>
      <TableCell className="font-mono text-xs">
        <span className="inline-flex items-center gap-1">
          <ClockIcon className="size-3" />
          {formatDuration(d.durationSeconds)}
        </span>
      </TableCell>
      <TableCell className="text-right" onClick={(e) => e.stopPropagation()}>
        {actions}
      </TableCell>
    </TableRow>
  );
}

// ─── Deployment stats bar ──────────────────────────────────────────────

function DeploymentStatsBar({ deployments }: { deployments: AstroliftDeployment[] }) {
  const stats = React.useMemo(() => {
    const total = deployments.length;
    const running = deployments.filter((d) => d.status === "running").length;
    const failed = deployments.filter((d) => d.status === "failed").length;
    const inFlight = deployments.filter((d) => IN_FLIGHT.has(d.status)).length;

    const successDenom = running + failed;
    const successRate = successDenom > 0 ? Math.round((running / successDenom) * 100) : null;

    const durations = deployments
      .map((d) => d.durationSeconds)
      .filter((v): v is number => v != null);
    const avgDuration =
      durations.length > 0
        ? Math.round(durations.reduce((sum, v) => sum + v, 0) / durations.length)
        : null;

    return { total, running, failed, inFlight, successRate, avgDuration };
  }, [deployments]);

  const successColor =
    stats.successRate == null
      ? "text-muted-foreground"
      : stats.successRate >= 80
        ? "text-success-fg"
        : stats.successRate >= 50
          ? "text-warning-fg"
          : "text-destructive";

  return (
    <div className="flex flex-wrap items-stretch gap-3">
      <div className="grid flex-1 grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard label="Deployments" value={stats.total.toString()} sub="in current view" />
        <StatCard
          label="Success rate"
          value={stats.successRate == null ? "—" : `${stats.successRate}%`}
          valueClassName={successColor}
          sub={`${stats.running} running`}
        />
        <StatCard
          label="Avg duration"
          value={stats.avgDuration == null ? "—" : formatDuration(stats.avgDuration)}
          sub="per deploy"
        />
        <StatCard
          label="Failed"
          value={stats.failed.toString()}
          valueClassName={stats.failed > 0 ? "text-destructive" : undefined}
          sub="need attention"
        />
      </div>
      <FrequencyBars deployments={deployments} />
    </div>
  );
}

function StatCard({
  label,
  value,
  sub,
  valueClassName,
}: {
  label: string;
  value: string;
  sub: string;
  valueClassName?: string;
}) {
  return (
    <div className="bg-muted/30 border-border rounded-lg border px-4 py-3">
      <div className="text-muted-foreground text-2xs font-medium tracking-wide uppercase">
        {label}
      </div>
      <div className={cn("text-xl font-semibold tabular-nums", valueClassName)}>{value}</div>
      <div className="text-muted-foreground text-2xs">{sub}</div>
    </div>
  );
}

function FrequencyBars({ deployments }: { deployments: AstroliftDeployment[] }) {
  const { buckets, max } = React.useMemo(() => {
    const days: { date: Date; key: string; count: number }[] = [];
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    for (let i = 6; i >= 0; i--) {
      const date = new Date(today);
      date.setDate(date.getDate() - i);
      const key = `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
      days.push({ date, key, count: 0 });
    }
    const keyByDate = new Map(days.map((d) => [d.key, d]));
    for (const d of deployments) {
      const iso = d.startedAt ?? d.createdAt;
      if (!iso) continue;
      const when = new Date(iso);
      when.setHours(0, 0, 0, 0);
      const key = `${when.getFullYear()}-${when.getMonth()}-${when.getDate()}`;
      const bucket = keyByDate.get(key);
      if (bucket) bucket.count += 1;
    }
    const maxCount = days.reduce((m, d) => Math.max(m, d.count), 0);
    return { buckets: days, max: maxCount };
  }, [deployments]);

  const total = buckets.reduce((sum, b) => sum + b.count, 0);

  return (
    <div className="bg-muted/30 border-border flex min-w-[200px] flex-col rounded-lg border px-4 py-3">
      <div className="text-muted-foreground text-2xs font-medium tracking-wide uppercase">
        Last 7 days
      </div>
      <div className="mt-2 flex h-10 items-end gap-1">
        {buckets.map((b) => {
          const height = max > 0 ? Math.max(4, Math.round((b.count / max) * 100)) : 4;
          return (
            <div
              key={b.key}
              className="flex flex-1 flex-col items-center justify-end"
              title={`${b.date.toLocaleDateString()}: ${b.count}`}
            >
              <div
                className={cn(
                  "w-full rounded-sm",
                  b.count > 0
                    ? "bg-[color:var(--brand-primary)]"
                    : "bg-[color:var(--brand-primary)]/50"
                )}
                style={{ height: `${height}%` }}
              />
            </div>
          );
        })}
      </div>
      <div className="text-muted-foreground text-2xs mt-1">{total} this week</div>
    </div>
  );
}
