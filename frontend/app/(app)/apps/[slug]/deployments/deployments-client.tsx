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
  LayersIcon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";

import { DeploymentStatusPill } from "@/components/DeploymentStatusPill";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { LIST_DEPLOYMENTS, LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type {
  AstroliftAppEnvironment,
  AstroliftDeployment,
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

                  const parent = (
                    <TableRow
                      key={`g-${g.key}`}
                      className="hover:bg-accent/30 cursor-pointer"
                      onClick={() => {
                        if (isMultiple) toggleGroup(g.key);
                        else window.location.href = `/deployments/${rep.id}`;
                      }}
                    >
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
    </PageShell>
  );
}
