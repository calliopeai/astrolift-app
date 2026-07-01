"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  BoxIcon,
  CheckCircle2Icon,
  CheckIcon,
  ClipboardIcon,
  ClockIcon,
  ExternalLinkIcon,
  GitCommitIcon,
  GitCompareIcon,
  RotateCcwIcon,
  StopCircleIcon,
  Trash2Icon,
  UndoIcon,
  XCircleIcon,
  XIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ConfirmDialogWithReason } from "@/components/ConfirmDialogWithReason";
import { DeploymentStatusPill } from "@/components/DeploymentStatusPill";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
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
import {
  ABORT_DEPLOYMENT,
  APPROVE_DEPLOYMENT,
  REDEPLOY_APP,
  ROLLBACK_DEPLOYMENT,
} from "@/graphql/lifecycle/lifecycle.mutations";
import {
  COMPARE_DEPLOYMENTS,
  GET_DEPLOYMENT_LOG,
  LIST_DEPLOYMENTS,
  LIST_ENVIRONMENTS,
} from "@/graphql/lifecycle/lifecycle.queries";
import type {
  AstroliftAppEnvironment,
  AstroliftDeployment,
  AstroliftDeploymentComparison,
  AstroliftDeploymentLogEntry,
  DeploymentStatus,
} from "@/graphql/lifecycle/lifecycle.types";
import { GET_APP, GET_RENDERED_MANIFEST } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";
import { cn } from "@/lib/utils";

import { AppTabs } from "../components/app-tabs";

interface AppResp {
  astroliftApp: AstroliftRegisteredApp | null;
}
interface DeploymentsResp {
  astroliftDeployments: AstroliftDeployment[];
}
interface EnvsResp {
  astroliftEnvironments: AstroliftAppEnvironment[];
}
interface LogResp {
  astroliftDeploymentLog: AstroliftDeploymentLogEntry[];
}
interface ManifestResp {
  astroliftRenderedManifest: {
    appSlug: string;
    environmentName?: string | null;
    imageTag?: string | null;
    namespace: string;
    resources: unknown;
    error?: string | null;
    errorPath?: string | null;
    errorLine?: number | null;
    errorColumn?: number | null;
  } | null;
}

interface MutationResultLite<T> {
  ok: boolean;
  errors: { code: string; message: string }[];
  data: T | null;
}

type StatusBucket = "all" | "succeeded" | "failed" | "in_flight" | "other";

const STATUS_BUCKET_KEYS: { value: StatusBucket; key: string }[] = [
  { value: "all", key: "all" },
  { value: "succeeded", key: "succeeded" },
  { value: "failed", key: "failed" },
  { value: "in_flight", key: "inFlight" },
  { value: "other", key: "other" },
];

const IN_FLIGHT: ReadonlySet<DeploymentStatus> = new Set([
  "pending_approval",
  "pending",
  "deploying",
  "redeploying",
]);
const FAILED: ReadonlySet<DeploymentStatus> = new Set(["failed"]);
const SUCCEEDED: ReadonlySet<DeploymentStatus> = new Set(["running"]);

// Number of visible columns in the deployments table. The inline-expand
// row uses this for its `colSpan` so the panel always spans the width
// of the table regardless of how many columns we render.
const TABLE_COLUMNS = 7;

const DEPLOYMENTS_PAGE_SIZE = 20;

function statusMatches(bucket: StatusBucket, status: DeploymentStatus): boolean {
  switch (bucket) {
    case "all":
      return true;
    case "succeeded":
      return SUCCEEDED.has(status);
    case "failed":
      return FAILED.has(status);
    case "in_flight":
      return IN_FLIGHT.has(status);
    case "other":
      return !SUCCEEDED.has(status) && !FAILED.has(status) && !IN_FLIGHT.has(status);
  }
}

function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}m ${s}s`;
}

function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}

function formatLogTime(iso: string | null | undefined): string {
  if (!iso) return "--:--:--";
  const d = new Date(iso);
  return d.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function githubCommitUrl(repo: string, sha: string): string {
  return `https://github.com/${repo}/commit/${sha}`;
}

export function AppDeploymentsClient({ slug }: { slug: string }) {
  const tCommon = useTranslations("apps.common");
  const t = useTranslations("apps.deployments");
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const app = useQuery<AppResp>(GET_APP, { variables: { slug } });
  const deployments = useQuery<DeploymentsResp>(LIST_DEPLOYMENTS, {
    variables: { appSlug: slug, limit: 100 },
    pollInterval: 30000,
  });
  const envs = useQuery<EnvsResp>(LIST_ENVIRONMENTS, {
    variables: { appSlug: slug },
  });

  // Filters and the open-row id are persisted to URL search params so a
  // page refresh or shared link preserves the operator's view exactly.
  const rawBucket = searchParams.get("status") as StatusBucket | null;
  const statusBucket: StatusBucket =
    rawBucket && STATUS_BUCKET_KEYS.some((k) => k.value === rawBucket) ? rawBucket : "all";
  const envFilter = searchParams.get("env") ?? "all";
  const openId = searchParams.get("open");

  const [search, setSearchLocal] = React.useState<string>(() => searchParams.get("q") ?? "");

  const writeParams = React.useCallback(
    (mutate: (p: URLSearchParams) => void) => {
      const params = new URLSearchParams(searchParams.toString());
      mutate(params);
      router.replace(`${pathname}?${params.toString()}`, { scroll: false });
    },
    [pathname, router, searchParams]
  );

  function updateFilter(key: string, value: string) {
    writeParams((params) => {
      if (value && value !== "all" && value !== "") {
        params.set(key, value);
      } else {
        params.delete(key);
      }
    });
  }

  const setStatusBucket = (v: StatusBucket) => updateFilter("status", v);
  const setEnvFilter = (v: string) => updateFilter("env", v);

  const setOpenId = React.useCallback(
    (id: string | null) => {
      writeParams((params) => {
        if (id) params.set("open", id);
        else params.delete("open");
      });
    },
    [writeParams]
  );

  const toggleOpen = React.useCallback(
    (id: string) => {
      setOpenId(openId === id ? null : id);
    },
    [openId, setOpenId]
  );

  // Debounce search → URL (300 ms gives snappy typing without thrashing history).
  React.useEffect(() => {
    const id = setTimeout(() => updateFilter("q", search), 300);
    return () => clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

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

  const a = app.data?.astroliftApp;
  const allDeployments = React.useMemo(
    () => deployments.data?.astroliftDeployments ?? [],
    [deployments.data?.astroliftDeployments]
  );
  const envList = envs.data?.astroliftEnvironments ?? [];

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

  return (
    <PageShell
      title={t("title", { name: a.name })}
      description={
        <span className="text-muted-foreground font-mono text-xs">
          {t("description", { slug: a.slug })}
        </span>
      }
    >
      <AppTabs slug={a.slug} active="deployments" />

      {allDeployments.length > 0 && !deployments.loading && (
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
            onChange={(e) => setSearchLocal(e.target.value)}
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
          {deployments.loading && allDeployments.length === 0 ? (
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
                actionHref={`/apps/${a.slug}/environments`}
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
                      app={a}
                    />,
                    isOpen ? (
                      <DeploymentExpandPanel
                        key={`${d.id}-panel`}
                        deployment={d}
                        app={a}
                        onClose={() => setOpenId(null)}
                      />
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
        <Link href={`/apps/${a.slug}/environments`} className="hover:text-foreground underline">
          {t("rollNew")}
        </Link>
        .
      </p>

      {canCompare && (
        <CompareDeploymentsSheet
          open={compareOpen}
          onOpenChange={setCompareOpen}
          deployA={selectedList[0]}
          deployB={selectedList[1]}
        />
      )}
    </PageShell>
  );
}

// ─── Row ───────────────────────────────────────────────────────────────

function DeploymentRow({
  deployment,
  isOpen,
  selected,
  onToggleSelect,
  onToggleOpen,
  app,
}: {
  deployment: AstroliftDeployment;
  isOpen: boolean;
  selected: boolean;
  onToggleSelect: () => void;
  onToggleOpen: () => void;
  app: AstroliftRegisteredApp;
}) {
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
      <TableCell>
        <DeploymentStatusPill status={d.status} />
      </TableCell>
      <TableCell>
        <div className="font-mono text-xs">{d.imageTag || "—"}</div>
        {d.workloadSlug && (
          <div className="text-muted-foreground mt-0.5 font-mono text-2xs">{d.workloadSlug}</div>
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
        <RowActions deployment={d} app={app} />
      </TableCell>
    </TableRow>
  );
}

// ─── Row actions ───────────────────────────────────────────────────────

function RowActions({
  deployment,
  app,
}: {
  deployment: AstroliftDeployment;
  app: AstroliftRegisteredApp;
}) {
  const { can } = useMyPermissions();
  const d = deployment;

  const refetch = [
    {
      query: LIST_DEPLOYMENTS,
      variables: { appSlug: app.slug, limit: 100 },
    },
  ];

  const [approve, approveState] = useMutation<{
    approveDeployment: MutationResultLite<AstroliftDeployment>;
  }>(APPROVE_DEPLOYMENT, { refetchQueries: refetch });
  const [abort, abortState] = useMutation<{
    abortDeployment: MutationResultLite<AstroliftDeployment>;
  }>(ABORT_DEPLOYMENT, { refetchQueries: refetch });
  const [rollback, rollbackState] = useMutation<{
    rollbackDeployment: MutationResultLite<AstroliftDeployment>;
  }>(ROLLBACK_DEPLOYMENT, { refetchQueries: refetch });
  const [redeploy, redeployState] = useMutation<{
    redeployApp: MutationResultLite<AstroliftDeployment>;
  }>(REDEPLOY_APP, { refetchQueries: refetch });

  const [confirmAbort, setConfirmAbort] = React.useState(false);
  const [confirmRollback, setConfirmRollback] = React.useState(false);
  const [confirmTrash, setConfirmTrash] = React.useState(false);

  const busy =
    approveState.loading || abortState.loading || rollbackState.loading || redeployState.loading;

  function reportResult(
    label: string,
    result: MutationResultLite<AstroliftDeployment> | null | undefined
  ) {
    if (!result) return;
    if (result.ok) {
      toast.success(`${label}: ${result.data?.status ?? "ok"}`);
    } else {
      throw new Error(result.errors[0]?.message ?? `${label} failed`);
    }
  }

  const showApprove =
    d.status === "pending_approval" &&
    d.approvalsReceived < d.approvalsRequired &&
    can("app.approve_deploy");
  const inFlight = IN_FLIGHT.has(d.status);
  const showAbort = inFlight && can("app.deploy");
  const showRedeploy = d.status === "running" && can("app.deploy");
  const showRollback =
    (d.status === "running" || d.status === "superseded" || d.status === "rolled_back") &&
    can("app.rollback");
  const showFailedRollback = d.status === "failed" && can("app.rollback");
  const showFailedTrash = d.status === "failed" && can("app.deploy");

  return (
    <div className="inline-flex items-center justify-end gap-1">
      {showApprove && (
        <Button
          size="sm"
          variant="default"
          disabled={busy}
          onClick={async () => {
            try {
              const { data } = await approve({
                variables: { input: { id: d.id } },
              });
              reportResult("approveDeployment", data?.approveDeployment);
            } catch (err) {
              toast.error(err instanceof Error ? err.message : "Approve failed");
            }
          }}
        >
          <CheckIcon className="size-3.5" />
          Approve
        </Button>
      )}
      {showRedeploy && (
        <Button
          size="sm"
          variant="outline"
          disabled={busy}
          onClick={async () => {
            try {
              const { data } = await redeploy({
                variables: { input: { id: d.id } },
              });
              reportResult("redeployApp", data?.redeployApp);
            } catch (err) {
              toast.error(err instanceof Error ? err.message : "Redeploy failed");
            }
          }}
        >
          <RotateCcwIcon className="size-3.5" />
          Redeploy
        </Button>
      )}
      {(showRollback || showFailedRollback) && (
        <Button
          size="sm"
          variant="outline"
          disabled={busy}
          onClick={() => setConfirmRollback(true)}
        >
          <UndoIcon className="size-3.5" />
          Rollback
        </Button>
      )}
      {showAbort && (
        <Button
          size="sm"
          variant="destructive"
          disabled={busy}
          onClick={() => setConfirmAbort(true)}
        >
          <StopCircleIcon className="size-3.5" />
          Abort
        </Button>
      )}
      {showFailedTrash && (
        <Button
          size="sm"
          variant="ghost"
          disabled={busy}
          aria-label="Discard failed deployment"
          onClick={() => setConfirmTrash(true)}
        >
          <Trash2Icon className="size-3.5" />
        </Button>
      )}

      <ConfirmDialogWithReason
        open={confirmAbort}
        onOpenChange={setConfirmAbort}
        title={`Abort deploy to ${d.environmentName}?`}
        description="The in-flight rollout will be marked failed. Tell the team what changed."
        reasonLabel="Reason for abort"
        reasonPlaceholder="Why are you aborting this deploy?"
        confirmLabel="Abort deploy"
        destructive
        onConfirm={async (reason) => {
          const { data } = await abort({
            variables: { input: { id: d.id, reason } },
          });
          reportResult("abortDeployment", data?.abortDeployment);
        }}
      />

      <ConfirmDialog
        open={confirmRollback}
        onOpenChange={setConfirmRollback}
        title={`Rollback ${d.environmentName} to ${d.imageTag || d.id.slice(0, 8)}?`}
        description="The platform will redeploy this image as the live version. The current rollout will be marked superseded."
        confirmLabel="Roll back"
        onConfirm={async () => {
          const { data } = await rollback({
            variables: { input: { id: d.id } },
          });
          reportResult("rollbackDeployment", data?.rollbackDeployment);
        }}
      />

      <ConfirmDialogWithReason
        open={confirmTrash}
        onOpenChange={setConfirmTrash}
        title={`Discard failed deploy ${d.imageTag || d.id.slice(0, 8)}?`}
        description="The row stays in history but the rollout is marked aborted. Tell the team what changed."
        reasonLabel="Reason for discard"
        reasonPlaceholder="Why are you discarding this deploy?"
        confirmLabel="Discard"
        destructive
        onConfirm={async (reason) => {
          const { data } = await abort({
            variables: { input: { id: d.id, reason } },
          });
          reportResult("abortDeployment", data?.abortDeployment);
        }}
      />
    </div>
  );
}

// ─── Inline expand panel ───────────────────────────────────────────────

function DeploymentExpandPanel({
  deployment,
  app,
  onClose,
}: {
  deployment: AstroliftDeployment;
  app: AstroliftRegisteredApp;
  onClose: () => void;
}) {
  const d = deployment;
  const log = useQuery<LogResp>(GET_DEPLOYMENT_LOG, {
    variables: { deploymentId: d.id },
    fetchPolicy: "cache-and-network",
  });
  const manifest = useQuery<ManifestResp>(GET_RENDERED_MANIFEST, {
    variables: {
      appSlug: d.registeredAppSlug,
      environmentName: d.environmentName,
      imageTag: d.imageTag || null,
    },
    fetchPolicy: "cache-first",
  });

  const success = d.status === "running";
  const failed = d.status === "failed";
  const env = useQuery<EnvsResp>(LIST_ENVIRONMENTS, {
    variables: { appSlug: d.registeredAppSlug },
    fetchPolicy: "cache-first",
  });
  const envObj = env.data?.astroliftEnvironments?.find((e) => e.name === d.environmentName);

  return (
    <TableRow className="bg-muted/20 hover:bg-muted/20">
      <TableCell colSpan={TABLE_COLUMNS} className="p-0">
        <div className="border-border/60 space-y-4 border-t px-6 py-4">
          {/* Header bar */}
          <div className="flex flex-wrap items-center gap-3">
            <DeploymentStatusPill status={d.status} />
            <span className="font-mono text-sm">{d.imageTag || d.id.slice(0, 12)}</span>
            <span className="text-muted-foreground text-xs">·</span>
            <span className="text-muted-foreground inline-flex items-center gap-1 text-xs">
              <ClockIcon className="size-3" />
              {formatDuration(d.durationSeconds)}
            </span>
            <span className="text-muted-foreground text-xs">·</span>
            <Badge variant="outline" className="font-mono text-xs">
              {d.environmentName}
            </Badge>
            <Button
              size="sm"
              variant="ghost"
              className="ml-auto"
              onClick={onClose}
              aria-label="Close panel"
            >
              <XIcon className="size-3.5" />
              Close
            </Button>
          </div>

          {/* Success / failure banner */}
          {success && (
            <div className="flex flex-wrap items-center gap-3 rounded-md border border-[color:var(--brand-primary)]/30 bg-[color:var(--brand-primary)]/10 px-3 py-2 text-sm">
              <CheckCircle2Icon className="size-4 text-[color:var(--brand-primary)]" />
              <span className="font-medium">Deployment successful!</span>
              {envObj?.url && (
                <a
                  href={envObj.url}
                  target="_blank"
                  rel="noreferrer"
                  className="ml-auto inline-flex items-center gap-1 text-xs font-medium text-[color:var(--brand-primary)] hover:underline"
                >
                  Open app
                  <ExternalLinkIcon className="size-3" />
                </a>
              )}
            </div>
          )}
          {failed && (
            <div className="border-destructive/30 bg-destructive/10 flex flex-wrap items-center gap-3 rounded-md border px-3 py-2 text-sm">
              <XCircleIcon className="text-destructive size-4" />
              <span className="font-medium">Deployment failed.</span>
              {d.abortedReason && (
                <span className="text-destructive/90 font-mono text-xs break-words">
                  {d.abortedReason}
                </span>
              )}
            </div>
          )}

          {/* Applied manifests */}
          <ManifestTabs
            manifest={manifest.data?.astroliftRenderedManifest}
            loading={manifest.loading}
          />

          {/* Deployment log */}
          <DeploymentLog entries={log.data?.astroliftDeploymentLog ?? []} loading={log.loading} />

          {/* Secondary commit / branch / CI metadata row */}
          <CommitMetaRow deployment={d} repoFullName={app.sourceRepo} />
        </div>
      </TableCell>
    </TableRow>
  );
}

// ─── Manifest tabs ─────────────────────────────────────────────────────

interface ManifestForTabs {
  resources: unknown;
  error?: string | null;
  errorPath?: string | null;
  errorLine?: number | null;
}

function ManifestTabs({
  manifest,
  loading,
}: {
  manifest: ManifestForTabs | null | undefined;
  loading: boolean;
}) {
  const tabs = React.useMemo(() => buildManifestTabs(manifest?.resources), [manifest?.resources]);
  // Track only the user's tab selection. The effective active tab falls
  // back to the first available kind whenever the user hasn't picked
  // one yet or the manifest shape changed and their selection no longer
  // exists — this avoids a setState-in-effect cascade.
  const [override, setOverride] = React.useState<string | null>(null);
  const activeTab = (override && tabs.find((t) => t.id === override)) || tabs[0] || null;
  const body = activeTab ? JSON.stringify(activeTab.payload, null, 2) : "";

  async function copyBody() {
    if (!body) return;
    try {
      await navigator.clipboard.writeText(body);
      toast.success("Manifest copied to clipboard");
    } catch {
      toast.error("Couldn't copy — clipboard access blocked");
    }
  }

  return (
    <section className="space-y-2">
      <h4 className="text-muted-foreground text-xs font-semibold tracking-wide uppercase">
        Applied manifests
      </h4>
      {loading && !manifest ? (
        <Skeleton className="h-32 w-full" />
      ) : manifest?.error ? (
        <div className="text-destructive text-xs">
          <p className="font-medium">Manifest could not be rendered.</p>
          <p className="mt-1 font-mono">{manifest.error}</p>
          {manifest.errorPath && (
            <p className="text-muted-foreground mt-1 font-mono">
              {manifest.errorPath}
              {manifest.errorLine != null && ` :${manifest.errorLine}`}
            </p>
          )}
        </div>
      ) : tabs.length === 0 ? (
        <p className="text-muted-foreground text-xs">No manifest available for this deployment.</p>
      ) : (
        <div className="space-y-2">
          <div className="border-border flex flex-wrap items-center gap-1 border-b">
            {tabs.map((t) => (
              <button
                key={t.id}
                type="button"
                onClick={() => setOverride(t.id)}
                className={cn(
                  "-mb-px border-b-2 px-3 py-1.5 text-xs font-medium transition-colors",
                  activeTab?.id === t.id
                    ? "border-foreground text-foreground"
                    : "text-muted-foreground hover:text-foreground border-transparent"
                )}
              >
                {t.label}
                {t.count > 1 && (
                  <span className="text-muted-foreground ml-1 tabular-nums">{t.count}</span>
                )}
              </button>
            ))}
            <Button
              size="sm"
              variant="ghost"
              className="ml-auto h-7"
              onClick={copyBody}
              disabled={!body}
            >
              <ClipboardIcon className="size-3.5" />
              Copy
            </Button>
          </div>
          <pre className="bg-muted max-h-96 overflow-auto rounded-md p-3 font-mono text-xs leading-relaxed">
            {body}
          </pre>
        </div>
      )}
    </section>
  );
}

interface ManifestTab {
  id: string;
  label: string;
  count: number;
  payload: unknown;
}

const KNOWN_K8S_KINDS = new Set([
  "Deployment",
  "StatefulSet",
  "DaemonSet",
  "Service",
  "Ingress",
  "ConfigMap",
  "Secret",
  "CronJob",
  "Job",
  "HorizontalPodAutoscaler",
  "ServiceAccount",
  "Role",
  "RoleBinding",
  "ClusterRole",
  "ClusterRoleBinding",
  "PersistentVolumeClaim",
  "NetworkPolicy",
  "PodDisruptionBudget",
]);

/**
 * Group rendered manifest resources by Kubernetes kind into one tab per
 * kind. Handles both shapes the backend produces:
 *   - an array of `{ kind, ...spec }` resources (the typical shape)
 *   - a `Record<kind, spec | spec[]>` keyed by kind (the legacy shape)
 *
 * Anything else falls back to a single "Manifest" tab carrying the raw
 * payload so the operator can still inspect / copy the rendered JSON.
 */
function buildManifestTabs(resources: unknown): ManifestTab[] {
  if (resources == null) return [];
  if (Array.isArray(resources)) {
    const groups = new Map<string, unknown[]>();
    for (const item of resources) {
      if (item && typeof item === "object" && "kind" in (item as Record<string, unknown>)) {
        const kind = String((item as { kind?: unknown }).kind ?? "Manifest");
        const bucket = groups.get(kind) ?? [];
        bucket.push(item);
        groups.set(kind, bucket);
      }
    }
    if (groups.size > 0) {
      return Array.from(groups.entries())
        .map(([kind, items]) => ({
          id: kind,
          label: kind,
          count: items.length,
          payload: items.length === 1 ? items[0] : items,
        }))
        .sort((a, b) => kindOrder(a.id) - kindOrder(b.id) || a.label.localeCompare(b.label));
    }
    // Array of opaque entries — fall through to the catch-all single tab.
    return [{ id: "manifest", label: "Manifest", count: resources.length, payload: resources }];
  }
  if (typeof resources === "object") {
    const record = resources as Record<string, unknown>;
    const keys = Object.keys(record);
    const k8sKeys = keys.filter((k) => KNOWN_K8S_KINDS.has(k));
    if (k8sKeys.length > 0) {
      return k8sKeys
        .map((kind) => {
          const value = record[kind];
          const count = Array.isArray(value) ? value.length : 1;
          return { id: kind, label: kind, count, payload: value };
        })
        .sort((a, b) => kindOrder(a.id) - kindOrder(b.id) || a.label.localeCompare(b.label));
    }
  }
  return [{ id: "manifest", label: "Manifest", count: 1, payload: resources }];
}

const KIND_ORDER: Record<string, number> = {
  Deployment: 0,
  StatefulSet: 1,
  DaemonSet: 2,
  CronJob: 3,
  Job: 4,
  Service: 10,
  Ingress: 11,
  HorizontalPodAutoscaler: 20,
  ConfigMap: 30,
  Secret: 31,
  ServiceAccount: 40,
  Role: 41,
  RoleBinding: 42,
  ClusterRole: 43,
  ClusterRoleBinding: 44,
  PersistentVolumeClaim: 50,
  NetworkPolicy: 60,
  PodDisruptionBudget: 70,
};

function kindOrder(kind: string): number {
  return KIND_ORDER[kind] ?? 100;
}

// ─── Deployment log ────────────────────────────────────────────────────

function DeploymentLog({
  entries,
  loading,
}: {
  entries: AstroliftDeploymentLogEntry[];
  loading: boolean;
}) {
  return (
    <section className="space-y-2">
      <h4 className="text-muted-foreground text-xs font-semibold tracking-wide uppercase">
        Deployment log
      </h4>
      {loading && entries.length === 0 ? (
        <Skeleton className="h-24 w-full" />
      ) : entries.length === 0 ? (
        <p className="text-muted-foreground text-xs">No log entries for this deployment yet.</p>
      ) : (
        <ol className="bg-muted/40 max-h-64 space-y-1 overflow-auto rounded-md p-3 font-mono text-xs leading-snug">
          {entries.map((e) => (
            <li key={e.id} className="flex flex-wrap items-baseline gap-2">
              <span className="text-muted-foreground tabular-nums">
                {formatLogTime(e.occurredAt)}
              </span>
              <Badge variant="outline" className="text-2xs capitalize">
                {e.status.replace(/_/g, " ")}
              </Badge>
              <span className="text-foreground break-words">{e.message || "—"}</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

// ─── Commit / branch / CI metadata row ─────────────────────────────────

function CommitMetaRow({
  deployment,
  repoFullName,
}: {
  deployment: AstroliftDeployment;
  repoFullName: string;
}) {
  const d = deployment;
  if (!d.commitSha && !d.branch && !d.commitAuthor && !d.ciRunUrl && !d.prNumber && !d.repoUrl) {
    return null;
  }
  const sha = d.commitSha ? d.commitSha.slice(0, 7) : null;
  return (
    <div className="text-muted-foreground flex flex-wrap items-center gap-x-4 gap-y-1 text-2xs">
      {sha && (
        <span className="inline-flex items-center gap-1">
          <GitCommitIcon className="size-3" />
          {repoFullName ? (
            <a
              href={githubCommitUrl(repoFullName, d.commitSha)}
              target="_blank"
              rel="noreferrer"
              className="hover:text-foreground font-mono hover:underline"
            >
              {sha}
            </a>
          ) : (
            <span className="font-mono">{sha}</span>
          )}
          {d.branch && <span className="text-muted-foreground/80">· {d.branch}</span>}
        </span>
      )}
      {d.commitAuthor && <span>by {d.commitAuthor}</span>}
      {d.prNumber > 0 && d.prUrl && (
        <a
          href={d.prUrl}
          target="_blank"
          rel="noreferrer"
          className="hover:text-foreground inline-flex items-center gap-1 hover:underline"
        >
          PR #{d.prNumber}
          <ExternalLinkIcon className="size-3" />
        </a>
      )}
      {d.ciRunUrl && (
        <a
          href={d.ciRunUrl}
          target="_blank"
          rel="noreferrer"
          className="hover:text-foreground inline-flex items-center gap-1 hover:underline"
        >
          {d.ciProvider || "ci"} run
          <ExternalLinkIcon className="size-3" />
        </a>
      )}
      {repoFullName && d.repoUrl && (
        <a
          href={d.repoUrl}
          target="_blank"
          rel="noreferrer"
          className="hover:text-foreground inline-flex items-center gap-1 hover:underline"
        >
          <span className="font-mono">{repoFullName}</span>
          <ExternalLinkIcon className="size-3" />
        </a>
      )}
    </div>
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
        <StatCard
          label="Deployments"
          value={stats.total.toString()}
          sub="in current view"
        />
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
      <div className="text-muted-foreground text-2xs font-medium uppercase tracking-wide">
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
      <div className="text-muted-foreground text-2xs font-medium uppercase tracking-wide">
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
      <div className="text-muted-foreground mt-1 text-2xs">{total} this week</div>
    </div>
  );
}

// ─── Compare sheet (#652) ──────────────────────────────────────────────

function CompareDeploymentsSheet({
  open,
  onOpenChange,
  deployA,
  deployB,
}: {
  open: boolean;
  onOpenChange: (next: boolean) => void;
  deployA: AstroliftDeployment;
  deployB: AstroliftDeployment;
}) {
  const { data, loading, error } = useQuery<{
    astroliftCompareDeployments: AstroliftDeploymentComparison | null;
  }>(COMPARE_DEPLOYMENTS, {
    variables: { idA: deployA.id, idB: deployB.id },
    skip: !open,
  });
  const cmp = data?.astroliftCompareDeployments ?? null;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col gap-4 sm:max-w-3xl">
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2 font-mono text-base">
            <GitCompareIcon className="size-4" />
            {deployA.imageTag || deployA.id.slice(0, 7)} →{" "}
            {deployB.imageTag || deployB.id.slice(0, 7)}
            {cmp?.compareUrl && (
              <a
                href={cmp.compareUrl}
                target="_blank"
                rel="noreferrer"
                className="text-muted-foreground hover:text-foreground ml-auto inline-flex items-center gap-1 text-xs"
              >
                View on source host
                <ExternalLinkIcon className="size-3" />
              </a>
            )}
          </SheetTitle>
          <SheetDescription>
            {cmp ? (
              <span className="font-mono text-xs">
                {cmp.baseSha.slice(0, 7)}...{cmp.headSha.slice(0, 7)}
              </span>
            ) : loading ? (
              "Loading comparison..."
            ) : error ? (
              <span className="text-destructive">{error.message}</span>
            ) : (
              "—"
            )}
          </SheetDescription>
        </SheetHeader>

        <div className="flex-1 space-y-5 overflow-y-auto px-4 pb-4">
          {loading && !cmp ? (
            <div className="space-y-2">
              <Skeleton className="h-6 w-full" />
              <Skeleton className="h-24 w-full" />
            </div>
          ) : cmp ? (
            <>
              <section className="space-y-2">
                <h4 className="text-sm font-semibold">Image diff</h4>
                {cmp.imageDiffSummary ? (
                  <pre className="bg-muted/40 border-border overflow-x-auto rounded-md border p-3 font-mono text-xs whitespace-pre-wrap">
                    {cmp.imageDiffSummary}
                  </pre>
                ) : (
                  <p className="text-muted-foreground text-xs">
                    No image diff available for this pair.
                  </p>
                )}
              </section>

              <section className="space-y-2">
                <h4 className="text-sm font-semibold">
                  Manifest diff{" "}
                  <span className="text-muted-foreground text-xs font-normal">
                    ({cmp.manifestDiff.length} change
                    {cmp.manifestDiff.length === 1 ? "" : "s"})
                  </span>
                </h4>
                {cmp.manifestDiff.length === 0 ? (
                  <p className="text-muted-foreground text-xs">
                    Manifests are identical between these two deploys.
                  </p>
                ) : (
                  <ul className="space-y-1.5">
                    {cmp.manifestDiff.map((entry, i) => (
                      <ManifestDiffRow key={`${entry.path}-${i}`} entry={entry} />
                    ))}
                  </ul>
                )}
              </section>
            </>
          ) : null}
        </div>
      </SheetContent>
    </Sheet>
  );
}

function ManifestDiffRow({
  entry,
}: {
  entry: { op: string; path: string; before: unknown; after: unknown };
}) {
  const badge =
    entry.op === "add" ? (
      <Badge className="border-success-border bg-success/15 text-success-fg">
        + add
      </Badge>
    ) : entry.op === "remove" ? (
      <Badge variant="destructive">− remove</Badge>
    ) : (
      <Badge className="border-warning-border bg-warning/15 text-warning-fg">
        ~ replace
      </Badge>
    );

  return (
    <li className="border-border bg-muted/20 space-y-1 rounded-md border p-2 font-mono text-xs">
      <div className="flex flex-wrap items-center gap-2">
        {badge}
        <code className="break-all">{entry.path}</code>
      </div>
      {entry.op === "add" && (
        <pre className="break-all whitespace-pre-wrap text-success-fg">
          {jsonValue(entry.after)}
        </pre>
      )}
      {entry.op === "remove" && (
        <pre className="text-destructive break-all whitespace-pre-wrap">
          {jsonValue(entry.before)}
        </pre>
      )}
      {entry.op === "replace" && (
        <div className="space-y-1">
          <pre className="text-destructive break-all whitespace-pre-wrap">
            − {jsonValue(entry.before)}
          </pre>
          <pre className="break-all whitespace-pre-wrap text-success-fg">
            + {jsonValue(entry.after)}
          </pre>
        </div>
      )}
    </li>
  );
}

function jsonValue(v: unknown): string {
  if (v === null || v === undefined) return "null";
  if (typeof v === "string") return v;
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}
