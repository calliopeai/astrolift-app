"use client";

import { useQuery } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  BoxIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  ClockIcon,
  ExternalLinkIcon,
  GitCommitIcon,
  GitCompareIcon,
  LayersIcon,
  XIcon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";

import { DeploymentStatusPill } from "@/components/DeploymentStatusPill";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
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
import {
  COMPARE_DEPLOYMENTS,
  LIST_DEPLOYMENTS,
  LIST_ENVIRONMENTS,
} from "@/graphql/lifecycle/lifecycle.queries";
import type {
  AstroliftAppEnvironment,
  AstroliftDeployment,
  AstroliftDeploymentComparison,
  DeploymentStatus,
  TriggerKind,
} from "@/graphql/lifecycle/lifecycle.types";
import { GET_APP } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

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

const statusToDot: Record<DeploymentStatus, "ok" | "warn" | "error" | "muted" | "pending"> = {
  pending_approval: "warn",
  pending: "warn",
  deploying: "pending",
  redeploying: "pending",
  running: "ok",
  failed: "error",
  superseded: "muted",
  rolled_back: "muted",
};

type StatusBucket = "all" | "succeeded" | "failed" | "in_flight" | "other";

const STATUS_BUCKET_KEYS: { value: StatusBucket; key: string }[] = [
  { value: "all", key: "all" },
  { value: "succeeded", key: "succeeded" },
  { value: "failed", key: "failed" },
  { value: "in_flight", key: "inFlight" },
  { value: "other", key: "other" },
];

const TRIGGER_KEYS: { value: TriggerKind | "all"; key: string }[] = [
  { value: "all", key: "all" },
  { value: "push", key: "push" },
  { value: "manual", key: "manual" },
  { value: "ci", key: "ci" },
  { value: "scheduled", key: "scheduled" },
  { value: "rollback", key: "rollback" },
  { value: "promotion", key: "promotion" },
];

const IN_FLIGHT: ReadonlySet<DeploymentStatus> = new Set([
  "pending_approval",
  "pending",
  "deploying",
  "redeploying",
]);
const FAILED: ReadonlySet<DeploymentStatus> = new Set(["failed"]);
const SUCCEEDED: ReadonlySet<DeploymentStatus> = new Set(["running"]);

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

function githubCommitUrl(repo: string, sha: string): string {
  return `https://github.com/${repo}/commit/${sha}`;
}

// #658 — collapse multi-workload deploys (FE + BE + combo for the same
// SHA → 3 rows today) into a single expandable parent.  Worst-status
// wins so a fan-out group surfaces as 'failed' the moment any child
// fails, no matter how many other children succeeded.  Groups are
// keyed by ``commitSha|environmentName`` so the same SHA deployed to
// two envs still shows as two separate top-level rows.
type GroupBy = "commit" | "flat";

type DeploymentGroupKey = string;

interface DeploymentGroup {
  key: DeploymentGroupKey;
  representative: AstroliftDeployment;
  items: AstroliftDeployment[];
}

const STATUS_RANK: Record<DeploymentStatus, number> = {
  failed: 5,
  pending_approval: 4,
  pending: 4,
  deploying: 4,
  redeploying: 4,
  running: 3,
  superseded: 2,
  rolled_back: 1,
};

function worstStatus(items: AstroliftDeployment[]): DeploymentStatus {
  return items.reduce<DeploymentStatus>((acc, d) => {
    return (STATUS_RANK[d.status] ?? 0) > (STATUS_RANK[acc] ?? 0) ? d.status : acc;
  }, items[0].status);
}

function groupDeployments(
  list: AstroliftDeployment[],
  mode: GroupBy
): DeploymentGroup[] {
  if (mode === "flat") {
    return list.map((d) => ({ key: d.id, representative: d, items: [d] }));
  }
  const buckets = new Map<DeploymentGroupKey, DeploymentGroup>();
  for (const d of list) {
    // Ungrouped buckets for rows with no SHA — manual/legacy entries.
    const key = d.commitSha
      ? `${d.commitSha}|${d.environmentName}`
      : `__nosha__|${d.id}`;
    const g = buckets.get(key);
    if (g) {
      g.items.push(d);
      // The representative tracks the newest startedAt so the parent
      // row's timestamp matches the latest workload's deploy.
      const a = new Date(d.startedAt ?? d.createdAt).getTime();
      const b = new Date(g.representative.startedAt ?? g.representative.createdAt).getTime();
      if (a > b) g.representative = d;
    } else {
      buckets.set(key, { key, representative: d, items: [d] });
    }
  }
  return Array.from(buckets.values());
}

export function AppDeploymentsClient({ slug }: { slug: string }) {
  const tCommon = useTranslations("apps.common");
  const t = useTranslations("apps.deployments");
  const app = useQuery<AppResp>(GET_APP, { variables: { slug } });
  const deployments = useQuery<DeploymentsResp>(LIST_DEPLOYMENTS, {
    variables: { appSlug: slug, limit: 100 },
    pollInterval: 30000,
  });
  const envs = useQuery<EnvsResp>(LIST_ENVIRONMENTS, {
    variables: { appSlug: slug },
  });

  const [statusBucket, setStatusBucket] = React.useState<StatusBucket>("all");
  const [triggerFilter, setTriggerFilter] = React.useState<TriggerKind | "all">("all");
  const [envFilter, setEnvFilter] = React.useState<string>("all");
  const [search, setSearch] = React.useState("");
  const [groupBy, setGroupBy] = React.useState<GroupBy>("commit");
  // Groups stay collapsed by default — the parent row already shows
  // SHA / env / aggregate status, so the operator only expands when
  // they want per-workload status.  Track expanded state keyed by
  // group key (commitSha|env), not deployment id.
  const [expanded, setExpanded] = React.useState<Set<DeploymentGroupKey>>(new Set());
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
      if (triggerFilter !== "all" && d.triggerKind !== triggerFilter) return false;
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
  }, [allDeployments, statusBucket, triggerFilter, envFilter, search]);

  const grouped = React.useMemo(() => groupDeployments(filtered, groupBy), [filtered, groupBy]);

  const toggleGroup = React.useCallback((key: DeploymentGroupKey) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const toggleSelect = React.useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  // Selectable rows = all individual deployments shown (flatten groups
  // for the master checkbox so it covers what the operator can see).
  const visibleIds = React.useMemo(() => {
    const ids: string[] = [];
    for (const g of grouped) {
      for (const item of g.items) ids.push(item.id);
    }
    return ids;
  }, [grouped]);

  const allVisibleSelected =
    visibleIds.length > 0 && visibleIds.every((id) => selectedIds.has(id));
  const someVisibleSelected =
    !allVisibleSelected && visibleIds.some((id) => selectedIds.has(id));

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

      {/* ─── filter bar ────────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-2">
        <Input
          placeholder={t("filters.search")}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="max-w-xs"
        />
        <Select value={statusBucket} onValueChange={(v) => setStatusBucket(v as StatusBucket)}>
          <SelectTrigger className="w-44">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {STATUS_BUCKET_KEYS.map((o) => (
              <SelectItem key={o.value} value={o.value}>
                {t(`statusBuckets.${o.key}`)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select
          value={triggerFilter}
          onValueChange={(v) => setTriggerFilter(v as TriggerKind | "all")}
        >
          <SelectTrigger className="w-40">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {TRIGGER_KEYS.map((o) => (
              <SelectItem key={o.value} value={o.value}>
                {t(`triggers.${o.key}`)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={envFilter} onValueChange={setEnvFilter}>
          <SelectTrigger className="w-40">
            <SelectValue placeholder={t("filters.anyEnv")} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t("filters.anyEnv")}</SelectItem>
            {envList.map((e) => (
              <SelectItem key={e.id} value={e.name}>
                {e.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={groupBy} onValueChange={(v) => setGroupBy(v as GroupBy)}>
          <SelectTrigger className="w-44">
            <LayersIcon className="mr-1 size-3.5" />
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="commit">Group by commit</SelectItem>
            <SelectItem value="flat">Flat (no grouping)</SelectItem>
          </SelectContent>
        </Select>
        <span className="text-muted-foreground ml-auto text-xs">
          {groupBy === "commit" && grouped.length !== filtered.length
            ? `${grouped.length} groups · ${filtered.length} of ${allDeployments.length}`
            : t("filters.counts", { filtered: filtered.length, total: allDeployments.length })}
        </span>
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
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setSelectedIds(new Set())}
          >
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
                  <TableHead className="w-6"></TableHead>
                  <TableHead>{t("table.when")}</TableHead>
                  <TableHead>{t("table.env")}</TableHead>
                  <TableHead>{t("table.imageCommit")}</TableHead>
                  <TableHead>{t("table.trigger")}</TableHead>
                  <TableHead>{t("table.status")}</TableHead>
                  <TableHead>{t("table.duration")}</TableHead>
                  <TableHead>{t("table.ci")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {grouped.flatMap((g) => {
                  const isMultiple = g.items.length > 1;
                  const isOpen = expanded.has(g.key);
                  const rep = g.representative;
                  const aggStatus = isMultiple ? worstStatus(g.items) : rep.status;
                  const aggDuration = isMultiple
                    ? g.items.reduce<number | null>((acc, d) => {
                        if (d.durationSeconds == null) return acc;
                        return acc == null ? d.durationSeconds : Math.max(acc, d.durationSeconds);
                      }, null)
                    : rep.durationSeconds;

                  // For a group, the parent-row checkbox selects/deselects
                  // every child deployment in the group (so the group's
                  // representative compare semantics still need an
                  // expanded single-deploy selection — the checkbox is a
                  // shortcut, not a primary path).
                  const allItemsSelected = g.items.every((d) => selectedIds.has(d.id));
                  const someItemsSelected =
                    !allItemsSelected && g.items.some((d) => selectedIds.has(d.id));
                  const parent = (
                    <TableRow
                      key={`g-${g.key}`}
                      className="hover:bg-accent/30 cursor-pointer"
                      onClick={() => {
                        if (isMultiple) toggleGroup(g.key);
                        else window.location.href = `/deployments/${rep.id}`;
                      }}
                    >
                      <TableCell className="w-8" onClick={(e) => e.stopPropagation()}>
                        <input
                          type="checkbox"
                          aria-label={`Select ${rep.imageTag ?? rep.id}`}
                          checked={allItemsSelected}
                          ref={(el) => {
                            if (el) el.indeterminate = someItemsSelected;
                          }}
                          onChange={() => {
                            setSelectedIds((prev) => {
                              const next = new Set(prev);
                              if (allItemsSelected) {
                                for (const d of g.items) next.delete(d.id);
                              } else {
                                for (const d of g.items) next.add(d.id);
                              }
                              return next;
                            });
                          }}
                        />
                      </TableCell>
                      <TableCell className="w-6">
                        {isMultiple ? (
                          isOpen ? (
                            <ChevronDownIcon className="size-3.5" />
                          ) : (
                            <ChevronRightIcon className="size-3.5" />
                          )
                        ) : (
                          <StatusDot status={statusToDot[rep.status]} />
                        )}
                      </TableCell>
                      <TableCell className="text-sm whitespace-nowrap">
                        {new Date(rep.startedAt ?? rep.createdAt).toLocaleString()}
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline" className="font-mono text-xs">
                          {rep.environmentName}
                        </Badge>
                        {isMultiple ? (
                          <div className="text-muted-foreground mt-1 inline-flex items-center gap-1 text-xs">
                            <LayersIcon className="size-3" />
                            {g.items.length} workloads
                          </div>
                        ) : (
                          rep.workloadSlug && (
                            <div className="text-muted-foreground mt-1 font-mono text-xs">
                              {rep.workloadSlug}
                            </div>
                          )
                        )}
                      </TableCell>
                      <TableCell>
                        <div className="font-mono text-xs">{rep.imageTag || "—"}</div>
                        {rep.commitSha && (
                          <div className="text-muted-foreground mt-0.5 inline-flex items-center gap-1 font-mono text-xs">
                            <GitCommitIcon className="size-3" />
                            {a.sourceKind === "github" && a.sourceRepo ? (
                              <a
                                href={githubCommitUrl(a.sourceRepo, rep.commitSha)}
                                target="_blank"
                                rel="noreferrer"
                                className="hover:underline"
                                onClick={(e) => e.stopPropagation()}
                              >
                                {rep.commitSha.slice(0, 7)}
                              </a>
                            ) : (
                              rep.commitSha.slice(0, 7)
                            )}
                            {rep.branch && (
                              <span className="text-muted-foreground/80">· {rep.branch}</span>
                            )}
                          </div>
                        )}
                        <div className="text-muted-foreground/80 mt-0.5 flex flex-wrap items-center gap-x-2 text-[11px]">
                          <span>{t("row.prMissing")}</span>
                          <span className="font-mono">
                            {rep.commitAuthor
                              ? t("row.authorBy", { name: rep.commitAuthor })
                              : t("row.authorMissing")}
                          </span>
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline" className="text-xs capitalize">
                          {rep.triggerKind}
                        </Badge>
                        {rep.strategy && rep.strategy !== "unknown" && (
                          <div className="text-muted-foreground mt-1 text-[10px] capitalize">
                            {rep.strategy.replace(/_/g, " ")}
                          </div>
                        )}
                      </TableCell>
                      <TableCell>
                        <DeploymentStatusPill status={aggStatus} />
                        {rep.approvalsRequired > 0 && (
                          <div className="text-muted-foreground mt-1 text-xs">
                            {t("approvalsCount", {
                              received: rep.approvalsReceived,
                              required: rep.approvalsRequired,
                            })}
                          </div>
                        )}
                      </TableCell>
                      <TableCell className="font-mono text-xs">
                        <span className="inline-flex items-center gap-1">
                          <ClockIcon className="size-3" />
                          {formatDuration(aggDuration)}
                        </span>
                      </TableCell>
                      <TableCell>
                        {rep.ciRunUrl ? (
                          <a
                            href={rep.ciRunUrl}
                            target="_blank"
                            rel="noreferrer"
                            className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-xs"
                            onClick={(e) => e.stopPropagation()}
                          >
                            {rep.ciProvider || "ci"}
                            <ExternalLinkIcon className="size-3" />
                          </a>
                        ) : (
                          <span className="text-muted-foreground text-xs">—</span>
                        )}
                      </TableCell>
                    </TableRow>
                  );

                  if (!isMultiple || !isOpen) return [parent];

                  const children = g.items.map((d) => (
                    <TableRow
                      key={d.id}
                      className="hover:bg-accent/30 bg-muted/30 cursor-pointer"
                      onClick={() => (window.location.href = `/deployments/${d.id}`)}
                    >
                      <TableCell className="w-8" onClick={(e) => e.stopPropagation()}>
                        <input
                          type="checkbox"
                          aria-label={`Select ${d.imageTag ?? d.id}`}
                          checked={selectedIds.has(d.id)}
                          onChange={() => toggleSelect(d.id)}
                        />
                      </TableCell>
                      <TableCell className="w-6 pl-8">
                        <StatusDot status={statusToDot[d.status]} />
                      </TableCell>
                      <TableCell className="text-muted-foreground text-xs whitespace-nowrap">
                        {new Date(d.startedAt ?? d.createdAt).toLocaleString()}
                      </TableCell>
                      <TableCell>
                        <div className="text-muted-foreground font-mono text-xs">
                          {d.workloadSlug || "—"}
                        </div>
                      </TableCell>
                      <TableCell>
                        <div className="font-mono text-xs">{d.imageTag || "—"}</div>
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline" className="text-xs capitalize">
                          {d.triggerKind}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <DeploymentStatusPill status={d.status} />
                      </TableCell>
                      <TableCell className="font-mono text-xs">
                        <span className="inline-flex items-center gap-1">
                          <ClockIcon className="size-3" />
                          {formatDuration(d.durationSeconds)}
                        </span>
                      </TableCell>
                      <TableCell>
                        {d.ciRunUrl ? (
                          <a
                            href={d.ciRunUrl}
                            target="_blank"
                            rel="noreferrer"
                            className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-xs"
                            onClick={(e) => e.stopPropagation()}
                          >
                            {d.ciProvider || "ci"}
                            <ExternalLinkIcon className="size-3" />
                          </a>
                        ) : (
                          <span className="text-muted-foreground text-xs">—</span>
                        )}
                      </TableCell>
                    </TableRow>
                  ));

                  return [parent, ...children];
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

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

// #652 — deploy-vs-deploy diff. The query is fired on open so a
// closed sheet doesn't keep paying for the comparison; the cache is
// keyed on (idA, idB) so reopening the same pair is a no-network hit.
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
              <span className="text-destructive">
                {error.message}
              </span>
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
      <Badge className="border-emerald-500/40 bg-emerald-500/15 text-emerald-700 dark:text-emerald-300">
        + add
      </Badge>
    ) : entry.op === "remove" ? (
      <Badge variant="destructive">− remove</Badge>
    ) : (
      <Badge className="border-amber-500/40 bg-amber-500/15 text-amber-700 dark:text-amber-300">
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
        <pre className="text-emerald-700 dark:text-emerald-300 whitespace-pre-wrap break-all">
          {jsonValue(entry.after)}
        </pre>
      )}
      {entry.op === "remove" && (
        <pre className="text-destructive whitespace-pre-wrap break-all">
          {jsonValue(entry.before)}
        </pre>
      )}
      {entry.op === "replace" && (
        <div className="space-y-1">
          <pre className="text-destructive whitespace-pre-wrap break-all">
            − {jsonValue(entry.before)}
          </pre>
          <pre className="text-emerald-700 dark:text-emerald-300 whitespace-pre-wrap break-all">
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
