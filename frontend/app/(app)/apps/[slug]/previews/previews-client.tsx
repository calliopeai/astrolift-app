"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  CalendarClockIcon,
  GitBranchIcon,
  GitPullRequestIcon,
  TrashIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ListControls } from "@/components/ListControls";
import { useListControls } from "@/hooks/use-list-controls";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { CopyBadge } from "@/components/CopyBadge";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
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
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import {
  CREATE_PREVIEW_ENVIRONMENT,
  EXTEND_PREVIEW_TTL,
  TEAR_DOWN_PREVIEW,
} from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_PREVIEW_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type {
  AstroliftPreviewEnvironment,
  PreviewStatus,
  PreviewTtlExtendDays,
} from "@/graphql/lifecycle/lifecycle.types";
import { GET_APP } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";
import { cn } from "@/lib/utils";

import { AppTabs } from "../components/app-tabs";

interface AppResp {
  astroliftApp: AstroliftRegisteredApp | null;
}
interface PreviewsResp {
  astroliftPreviewEnvironments: AstroliftPreviewEnvironment[];
}
interface MutationResultLite {
  ok: boolean;
  errors: { code: string; message: string }[];
}

const statusToDot: Record<PreviewStatus, "ok" | "warn" | "error" | "muted" | "pending"> = {
  building: "pending",
  running: "ok",
  failed: "error",
  torn_down: "muted",
};

// Stale-after threshold: anything still running this long after its last
// deploy is a candidate for cleanup. 7d is the default "feature work that
// should have merged by now" window — orgs that want longer can ignore
// the CTA, orgs that want shorter aren't penalized.
const STALE_DAYS = 7;
const STALE_THRESHOLD_MS = STALE_DAYS * 24 * 60 * 60 * 1000;

// Status-filter buttons in the list header (#431 scope B). "active"
// (default) hides torn-down rows since drowning in them is the #1
// reported friction; "all" lets an operator audit teardown history
// without leaving the page.
type StatusFilter = "active" | "running" | "failed" | "all";

const STATUS_FILTERS: StatusFilter[] = ["active", "running", "failed", "all"];

function matchesFilter(p: AstroliftPreviewEnvironment, filter: StatusFilter): boolean {
  switch (filter) {
    case "active":
      return p.status !== "torn_down";
    case "running":
      return p.status === "running";
    case "failed":
      return p.status === "failed";
    case "all":
      return true;
  }
}

function isStale(p: AstroliftPreviewEnvironment): boolean {
  if (p.status !== "running") return false;
  if (!p.lastDeployedAt) return false;
  const last = new Date(p.lastDeployedAt).getTime();
  return Date.now() - last > STALE_THRESHOLD_MS;
}

// Live countdown. Returns "expired" when ttl_until is in the past, an
// "Xd Yh" / "Xh Ym" / "Xm" string otherwise. Granularity drops as the
// window shrinks so the column doesn't show "0d 5h 23m 14s" on a
// preview about to be torn down.
function formatCountdown(ttlIsoString: string, now: number): { label: string; expired: boolean } {
  const target = new Date(ttlIsoString).getTime();
  const delta = target - now;
  if (delta <= 0) return { label: "expired", expired: true };
  const seconds = Math.floor(delta / 1000);
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  if (days >= 1) return { label: `${days}d ${hours}h`, expired: false };
  if (hours >= 1) return { label: `${hours}h ${mins}m`, expired: false };
  return { label: `${mins}m`, expired: false };
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
// the platform. Lives in the in-app resources index (not an external
// site) so operators stay inside the app.
const DOCS_PREVIEWS_HREF = "/resources/docs#previews";

// #659 — manual preview spin-up. Operators provision a preview from
// any branch without opening a PR; the backend uses the same workflow
// as the auto-preview path so TTL + resource limits are identical.
// Gated client-side on `app.deploy`; the resolver also enforces.
function CreatePreviewSheet({ appSlug, disabled }: { appSlug: string; disabled: boolean }) {
  const [open, setOpen] = React.useState(false);
  const [branch, setBranch] = React.useState("");

  const [createPreview, { loading }] = useMutation<{
    createPreviewEnvironment: MutationResultLite;
  }>(CREATE_PREVIEW_ENVIRONMENT, {
    refetchQueries: [{ query: LIST_PREVIEW_ENVIRONMENTS, variables: { appSlug } }],
  });

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const b = branch.trim();
    if (!b) return;
    const { data } = await createPreview({
      variables: { input: { appSlug, branch: b } },
    });
    const r = data?.createPreviewEnvironment;
    if (r?.ok) {
      toast.success(`Preview for "${b}" is provisioning`);
      setOpen(false);
      setBranch("");
    } else {
      toast.error(r?.errors[0]?.message ?? "Failed to create preview");
    }
  }

  return (
    <>
      <Can permission="app.deploy">
        <Button size="sm" onClick={() => setOpen(true)} disabled={disabled}>
          <GitBranchIcon className="size-3" /> Create preview
        </Button>
      </Can>
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent className="flex flex-col">
          <SheetHeader>
            <SheetTitle>Create preview environment</SheetTitle>
            <SheetDescription>
              Provision a preview from any branch without opening a pull request.
            </SheetDescription>
          </SheetHeader>
          <form onSubmit={handleSubmit} className="flex flex-1 flex-col gap-4 px-4 pb-4">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="preview-branch">Branch name</Label>
              <Input
                id="preview-branch"
                placeholder="feature/my-branch"
                value={branch}
                onChange={(e) => setBranch(e.target.value)}
                autoFocus
              />
              <p className="text-muted-foreground text-xs">
                The branch must exist in the connected repository. The environment will use the
                same TTL and resource limits as auto-preview environments.
              </p>
            </div>
            <SheetFooter>
              <Button type="button" variant="outline" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={loading || !branch.trim()}>
                {loading ? "Creating…" : "Create preview"}
              </Button>
            </SheetFooter>
          </form>
        </SheetContent>
      </Sheet>
    </>
  );
}

export function AppPreviewsClient({ slug }: { slug: string }) {
  const tCommon = useTranslations("apps.common");
  const t = useTranslations("apps.previews");
  const app = useQuery<AppResp>(GET_APP, { variables: { slug } });
  const previews = useQuery<PreviewsResp>(LIST_PREVIEW_ENVIRONMENTS, {
    variables: { appSlug: slug },
    pollInterval: 30000,
    fetchPolicy: "cache-and-network",
  });

  const [tearDown, tearState] = useMutation<{
    tearDownPreview: MutationResultLite;
  }>(TEAR_DOWN_PREVIEW, {
    refetchQueries: [{ query: LIST_PREVIEW_ENVIRONMENTS, variables: { appSlug: slug } }],
  });

  const [extendTtl, extendState] = useMutation<{
    extendPreviewTtl: MutationResultLite;
  }>(EXTEND_PREVIEW_TTL, {
    refetchQueries: [{ query: LIST_PREVIEW_ENVIRONMENTS, variables: { appSlug: slug } }],
  });

  const a = app.data?.astroliftApp;
  const list = previews.data?.astroliftPreviewEnvironments ?? [];

  const [statusFilter, setStatusFilter] = React.useState<StatusFilter>("active");
  const statusFiltered = React.useMemo(
    () => list.filter((p) => matchesFilter(p, statusFilter)),
    [list, statusFilter]
  );
  const ctrl = useListControls({
    data: statusFiltered,
    searchFn: (p) => [p.branch, String(p.prNumber), p.commitSha].join(" "),
    initialPageSize: 25,
    sortFn: (a, b, s) => {
      const dir = s.dir === "asc" ? 1 : -1;
      if (s.key === "branch") return a.branch.localeCompare(b.branch) * dir;
      if (s.key === "status") return a.status.localeCompare(b.status) * dir;
      return 0;
    },
  });
  const filtered = ctrl.rows;

  const counts = React.useMemo(
    () => ({
      running: list.filter((p) => p.status === "running").length,
      building: list.filter((p) => p.status === "building").length,
      failed: list.filter((p) => p.status === "failed").length,
      tornDown: list.filter((p) => p.status === "torn_down").length,
      total: list.length,
    }),
    [list]
  );

  // #660 — monthly preview-spend roll-up. estimatedDailyCostUsd is
  // null for previews the cost driver can't price (e.g. clusters
  // without a billing plugin), so we sum only what's available and
  // surface the count of unpriced rows next to the dollar figure.
  const spend = React.useMemo(() => {
    const live = list.filter((p) => p.status !== "torn_down");
    let dailySum = 0;
    let priced = 0;
    let unpriced = 0;
    for (const p of live) {
      if (typeof p.estimatedDailyCostUsd === "number") {
        dailySum += p.estimatedDailyCostUsd;
        priced += 1;
      } else {
        unpriced += 1;
      }
    }
    const today = new Date();
    const daysInMonth = new Date(today.getFullYear(), today.getMonth() + 1, 0).getDate();
    return {
      dailyTotal: dailySum,
      monthlyProjection: dailySum * daysInMonth,
      priced,
      unpriced,
      liveCount: live.length,
    };
  }, [list]);

  const stale = list.filter((p) => p.status !== "torn_down").filter(isStale);
  const [tearDownTarget, setTearDownTarget] = React.useState<AstroliftPreviewEnvironment | null>(
    null
  );

  // Live countdown ticker. Re-renders every 60s — that's the right
  // cadence for "5d 3h" granularity; faster updates would flash the
  // column without changing anything operators care about.
  const [now, setNow] = React.useState<number>(() => Date.now());
  React.useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 60_000);
    return () => clearInterval(id);
  }, []);

  if (app.loading && !a) {
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

  async function handleTearDown(p: AstroliftPreviewEnvironment) {
    const { data } = await tearDown({ variables: { input: { id: p.id } } });
    const r = data?.tearDownPreview;
    if (r?.ok) {
      toast.success(t("toasts.teardownEnqueued", { pr: p.prNumber }));
    } else {
      throw new Error(r?.errors[0]?.message ?? t("toasts.teardownFailed"));
    }
  }

  async function handleExtend(p: AstroliftPreviewEnvironment, days: PreviewTtlExtendDays) {
    const { data } = await extendTtl({
      variables: { input: { id: p.id, days } },
    });
    const r = data?.extendPreviewTtl;
    if (r?.ok) {
      toast.success(t("toasts.ttlExtended", { pr: p.prNumber, days }));
    } else {
      toast.error(r?.errors[0]?.message ?? t("toasts.extendFailed"));
    }
  }

  const isEmpty = list.length === 0;

  return (
    <PageShell
      title={t("title", { name: a.name })}
      description={
        <span className="text-muted-foreground font-mono text-xs">
          {t("description", { slug: a.slug, subdomain: a.subdomain })}
        </span>
      }
    >
      <AppTabs slug={a.slug} active="previews" />

      <div className="flex justify-end">
        <CreatePreviewSheet appSlug={a.slug} disabled={!a.previewEnabled} />
      </div>

      {!a.previewEnabled && (
        <Card className="border-amber-500/30 bg-amber-500/5">
          <CardHeader className="flex flex-row items-start gap-3 space-y-0 pb-3">
            <AlertTriangleIcon className="mt-0.5 size-4 text-amber-700 dark:text-amber-300" />
            <div className="flex-1">
              <CardTitle className="text-sm">{t("disabled.title")}</CardTitle>
              <CardDescription>{t("disabled.description")}</CardDescription>
            </div>
          </CardHeader>
        </Card>
      )}

      {stale.length > 0 && (
        <Card className="border-amber-500/30 bg-amber-500/5">
          <CardHeader className="flex flex-row items-start gap-3 space-y-0 pb-3">
            <CalendarClockIcon className="mt-0.5 size-4 text-amber-700 dark:text-amber-300" />
            <div className="flex-1">
              <CardTitle className="text-sm">{t("stale.title", { count: stale.length })}</CardTitle>
              <CardDescription>{t("stale.description", { days: STALE_DAYS })}</CardDescription>
            </div>
          </CardHeader>
        </Card>
      )}

      {/* #660 — monthly preview spend roll-up.  Only renders when at
          least one live preview is priced.  Surfaces dailyTotal +
          monthly projection + an unpriced-count caveat so the
          operator knows the figure is partial when drivers haven't
          returned a price. */}
      {spend.liveCount > 0 && (spend.priced > 0 || spend.unpriced > 0) && (
        <Card className="border-muted">
          <CardContent className="flex flex-wrap items-baseline gap-x-6 gap-y-2 px-6 py-4 text-sm">
            <div>
              <span className="text-muted-foreground text-xs">Daily spend</span>
              <div className="font-mono text-lg">
                ${spend.dailyTotal.toFixed(2)}
                <span className="text-muted-foreground ml-1 text-xs">/ day</span>
              </div>
            </div>
            <div>
              <span className="text-muted-foreground text-xs">This month (projected)</span>
              <div className="font-mono text-lg">
                ${spend.monthlyProjection.toFixed(2)}
              </div>
            </div>
            <div className="text-muted-foreground ml-auto text-xs">
              {spend.priced} of {spend.liveCount} previews priced
              {spend.unpriced > 0 && ` · ${spend.unpriced} not priced by this cluster's driver`}
            </div>
          </CardContent>
        </Card>
      )}

      <Card>
        {!isEmpty && (
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
            <div className="flex flex-wrap items-center gap-2">
              {STATUS_FILTERS.map((f) => (
                <Button
                  key={f}
                  variant={statusFilter === f ? "default" : "outline"}
                  size="sm"
                  onClick={() => setStatusFilter(f)}
                  className="h-7 text-xs"
                  title={t(`filterTooltips.${f}`)}
                >
                  {t(`filters.${f}`)}
                  <span className="text-muted-foreground ml-1">
                    {f === "active"
                      ? counts.running + counts.failed + counts.building
                      : f === "running"
                        ? counts.running
                        : f === "failed"
                          ? counts.failed
                          : counts.total}
                  </span>
                </Button>
              ))}
            </div>
            <div className="flex items-center gap-3">
              <div className="text-muted-foreground text-xs">
                {t("groupSummary", {
                  running: counts.running,
                  failed: counts.failed,
                  tornDown: counts.tornDown,
                })}
              </div>
              {statusFiltered.length > 0 && (
                <ListControls controls={ctrl} searchPlaceholder="Search by branch or PR…" hideSearch={false} />
              )}
            </div>
          </CardHeader>
        )}

        <CardContent className="p-0">
          {previews.loading && list.length === 0 ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : isEmpty ? (
            <div className="p-6">
              <EmptyState
                icon={<GitPullRequestIcon className="size-5" />}
                title={t("empty.title")}
                description={a.previewEnabled ? t("empty.enabledHint") : t("empty.disabledHint")}
                actionHref={
                  a.previewEnabled ? (a.sourceUrl ?? undefined) : `/apps/${a.slug}/config`
                }
                actionLabel={
                  a.previewEnabled ? (a.sourceUrl ? t("openRepo") : undefined) : t("enablePreviews")
                }
                learnMoreHref={DOCS_PREVIEWS_HREF}
                learnMoreLabel={t("empty.learnMore")}
                secondary={<EmptyStateSteps />}
              />
            </div>
          ) : filtered.length === 0 ? (
            <div className="text-muted-foreground p-10 text-center text-sm">{t("filterEmpty")}</div>
          ) : (
            <TooltipProvider delayDuration={200}>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-6"></TableHead>
                    <TableHead>{t("columns.pr")}</TableHead>
                    <TableHead>{t("columns.hostname")}</TableHead>
                    <TableHead>{t("columns.ttl")}</TableHead>
                    <TableHead>{t("columns.footprint")}</TableHead>
                    <TableHead>{t("columns.lastDeploy")}</TableHead>
                    <TableHead>{t("columns.status")}</TableHead>
                    <TableHead className="text-right">{t("columns.actions")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filtered.map((p) => (
                    <PreviewRow
                      key={p.id}
                      preview={p}
                      now={now}
                      tearLoading={tearState.loading}
                      extendLoading={extendState.loading}
                      onTearDown={() => setTearDownTarget(p)}
                      onExtend={(days) => handleExtend(p, days)}
                    />
                  ))}
                </TableBody>
              </Table>
            </TooltipProvider>
          )}
        </CardContent>
      </Card>

      {!isEmpty && (
        <p className="text-muted-foreground text-center text-xs">
          {t("footer", {
            count: a.previewMaxActive,
            plural: a.previewMaxActive === 1 ? "" : "s",
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
    </PageShell>
  );
}

// ---- row -------------------------------------------------------------

interface PreviewRowProps {
  preview: AstroliftPreviewEnvironment;
  now: number;
  tearLoading: boolean;
  extendLoading: boolean;
  onTearDown: () => void;
  onExtend: (days: PreviewTtlExtendDays) => void | Promise<void>;
}

function PreviewRow({
  preview: p,
  now,
  tearLoading,
  extendLoading,
  onTearDown,
  onExtend,
}: PreviewRowProps) {
  const t = useTranslations("apps.previews");
  const countdown = formatCountdown(p.ttlUntil, now);
  const ttlDate = new Date(p.ttlUntil);
  const aggregate = p.aggregateResources;
  const hasFootprint = aggregate.podCount > 0;
  const dailyCost = p.estimatedDailyCostUsd;
  const teardown = p.status !== "torn_down";

  return (
    <TableRow>
      <TableCell className="w-6">
        <StatusDot status={statusToDot[p.status]} />
      </TableCell>
      <TableCell>
        {p.prNumber > 0 ? (
          p.prUrl ? (
            <a
              href={p.prUrl}
              target="_blank"
              rel="noreferrer"
              className="font-medium hover:underline"
              title={t("openPr", { pr: p.prNumber })}
            >
              #{p.prNumber}
            </a>
          ) : (
            <span className="font-medium">#{p.prNumber}</span>
          )
        ) : (
          <span className="font-medium">{p.branch}</span>
        )}
        {p.prNumber > 0 && <span className="text-muted-foreground"> · {p.branch}</span>}
        <div className="text-muted-foreground font-mono text-xs">
          ns {p.namespace}
          {p.commitSha && <> · {p.commitSha.slice(0, 7)}</>}
        </div>
        {p.isManual && (
          <Badge variant="outline" className="mt-1 text-[10px] uppercase">
            manual
          </Badge>
        )}
      </TableCell>
      <TableCell>
        {p.status === "running" ? (
          <CopyBadge
            value={p.hostname}
            openHref={`https://${p.hostname}`}
            openLabel={t("openInTab")}
            title={t("copyHostname")}
          />
        ) : (
          <span className="text-muted-foreground font-mono text-xs">{p.hostname}</span>
        )}
      </TableCell>
      <TableCell>
        {teardown ? (
          <div className="flex flex-col gap-1">
            <Tooltip>
              <TooltipTrigger asChild>
                <span
                  className={cn(
                    "font-mono text-xs",
                    countdown.expired ? "text-rose-600 dark:text-rose-400" : "text-muted-foreground"
                  )}
                >
                  {countdown.label}
                </span>
              </TooltipTrigger>
              <TooltipContent>{t("ttlTooltip", { date: ttlDate.toLocaleString() })}</TooltipContent>
            </Tooltip>
            <Can permission="app.deploy">
              <div className="flex items-center gap-1">
                <Button
                  variant="outline"
                  size="sm"
                  className="h-6 px-2 text-xs"
                  disabled={extendLoading}
                  onClick={() => onExtend(1)}
                >
                  {t("extend.1d")}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  className="h-6 px-2 text-xs"
                  disabled={extendLoading}
                  onClick={() => onExtend(7)}
                >
                  {t("extend.7d")}
                </Button>
              </div>
            </Can>
          </div>
        ) : (
          <span className="text-muted-foreground text-xs">—</span>
        )}
      </TableCell>
      <TableCell>
        {hasFootprint ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <div className="cursor-default">
                <div className="text-xs">
                  {formatCpuCores(aggregate.cpuCores)} · {formatMemoryBytes(aggregate.memoryBytes)}
                </div>
                <div className="text-muted-foreground text-xs">
                  {dailyCost != null
                    ? t("costPerDay", { cost: dailyCost.toFixed(2) })
                    : t("costUnavailable")}
                </div>
              </div>
            </TooltipTrigger>
            <TooltipContent>
              {dailyCost != null
                ? t("costTooltip", { pods: aggregate.podCount, cost: dailyCost.toFixed(2) })
                : t("costTooltipUnavailable", { pods: aggregate.podCount })}
            </TooltipContent>
          </Tooltip>
        ) : (
          <span className="text-muted-foreground text-xs">—</span>
        )}
      </TableCell>
      <TableCell className="text-muted-foreground text-sm">
        {p.lastDeployedAt ? new Date(p.lastDeployedAt).toLocaleString() : "—"}
        {isStale(p) && (
          <Badge variant="outline" className="ml-2 text-[10px] uppercase">
            {t("staleBadge")}
          </Badge>
        )}
      </TableCell>
      <TableCell>
        <Badge variant="secondary" className="capitalize">
          {p.status.replace(/_/g, " ")}
        </Badge>
      </TableCell>
      <TableCell className="text-right">
        {teardown && (
          <Can permission="app.deploy">
            <Button size="sm" variant="outline" disabled={tearLoading} onClick={onTearDown}>
              <TrashIcon className="size-3" /> {t("tearDown")}
            </Button>
          </Can>
        )}
      </TableCell>
    </TableRow>
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
