"use client";

import { useQuery } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  BoxIcon,
  ClockIcon,
  ExternalLinkIcon,
  GitCommitIcon,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";

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
import {
  LIST_DEPLOYMENTS,
  LIST_ENVIRONMENTS,
} from "@/graphql/lifecycle/lifecycle.queries";
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

const statusToDot: Record<
  DeploymentStatus,
  "ok" | "warn" | "error" | "muted" | "pending"
> = {
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

const STATUS_BUCKET_OPTIONS: { value: StatusBucket; label: string }[] = [
  { value: "all", label: "Any status" },
  { value: "succeeded", label: "Succeeded" },
  { value: "failed", label: "Failed" },
  { value: "in_flight", label: "In flight" },
  { value: "other", label: "Rolled back / superseded" },
];

const TRIGGER_OPTIONS: { value: TriggerKind | "all"; label: string }[] = [
  { value: "all", label: "Any trigger" },
  { value: "push", label: "push" },
  { value: "manual", label: "manual" },
  { value: "ci", label: "ci" },
  { value: "scheduled", label: "scheduled" },
  { value: "rollback", label: "rollback" },
  { value: "promotion", label: "promotion" },
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

export function AppDeploymentsClient({ slug }: { slug: string }) {
  const app = useQuery<AppResp>(GET_APP, { variables: { slug } });
  const deployments = useQuery<DeploymentsResp>(LIST_DEPLOYMENTS, {
    variables: { appSlug: slug, limit: 100 },
    pollInterval: 30000,
  });
  const envs = useQuery<EnvsResp>(LIST_ENVIRONMENTS, {
    variables: { appSlug: slug },
  });

  const [statusBucket, setStatusBucket] =
    React.useState<StatusBucket>("all");
  const [triggerFilter, setTriggerFilter] = React.useState<TriggerKind | "all">(
    "all",
  );
  const [envFilter, setEnvFilter] = React.useState<string>("all");
  const [search, setSearch] = React.useState("");

  const a = app.data?.astroliftApp;
  const allDeployments = React.useMemo(
    () => deployments.data?.astroliftDeployments ?? [],
    [deployments.data?.astroliftDeployments],
  );
  const envList = envs.data?.astroliftEnvironments ?? [];

  const filtered = React.useMemo(() => {
    return allDeployments.filter((d) => {
      if (!statusMatches(statusBucket, d.status)) return false;
      if (triggerFilter !== "all" && d.triggerKind !== triggerFilter)
        return false;
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

  if (app.loading && !a) {
    return (
      <PageShell title="Loading…">
        <Skeleton className="h-32 w-full" />
      </PageShell>
    );
  }

  if (!a) {
    return (
      <PageShell title="App not found">
        <EmptyState
          icon={<AlertTriangleIcon className="size-5" />}
          title={`No app with slug ${slug}`}
          description="It may have been soft-deleted, or you may not have permission to read it."
          actionHref="/apps"
          actionLabel="Back to apps"
        />
      </PageShell>
    );
  }

  return (
    <PageShell
      title={`${a.name} · Deployments`}
      description={
        <span className="text-muted-foreground font-mono text-xs">
          {a.slug} · full historical deploy log with filters
        </span>
      }
    >
      <AppTabs slug={a.slug} active="deployments" />

      {/* ─── filter bar ────────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-2">
        <Input
          placeholder="Search commit, tag, branch, workload…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="max-w-xs"
        />
        <Select
          value={statusBucket}
          onValueChange={(v) => setStatusBucket(v as StatusBucket)}
        >
          <SelectTrigger className="w-44">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {STATUS_BUCKET_OPTIONS.map((o) => (
              <SelectItem key={o.value} value={o.value}>
                {o.label}
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
            {TRIGGER_OPTIONS.map((o) => (
              <SelectItem key={o.value} value={o.value}>
                {o.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={envFilter} onValueChange={setEnvFilter}>
          <SelectTrigger className="w-40">
            <SelectValue placeholder="Any env" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Any env</SelectItem>
            {envList.map((e) => (
              <SelectItem key={e.id} value={e.name}>
                {e.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span className="text-muted-foreground ml-auto text-xs">
          {filtered.length} of {allDeployments.length}
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
                title="No deployments match your filters"
                description={
                  allDeployments.length === 0
                    ? "This app hasn't been deployed yet."
                    : "Loosen the filters or clear the search to see more results."
                }
                actionHref={`/apps/${a.slug}/environments`}
                actionLabel={
                  allDeployments.length === 0 ? "Start a deployment" : undefined
                }
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-6"></TableHead>
                  <TableHead>When</TableHead>
                  <TableHead>Env</TableHead>
                  <TableHead>Image / commit</TableHead>
                  <TableHead>Trigger</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Duration</TableHead>
                  <TableHead>CI</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.map((d) => (
                  <TableRow
                    key={d.id}
                    className="hover:bg-accent/30 cursor-pointer"
                    onClick={() =>
                      (window.location.href = `/deployments/${d.id}`)
                    }
                  >
                    <TableCell className="w-6">
                      <StatusDot status={statusToDot[d.status]} />
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-sm">
                      {new Date(d.startedAt ?? d.createdAt).toLocaleString()}
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline" className="font-mono text-xs">
                        {d.environmentName}
                      </Badge>
                      {d.workloadSlug && (
                        <div className="text-muted-foreground mt-1 font-mono text-xs">
                          {d.workloadSlug}
                        </div>
                      )}
                    </TableCell>
                    <TableCell>
                      <div className="font-mono text-xs">
                        {d.imageTag || "—"}
                      </div>
                      {d.commitSha && (
                        <div className="text-muted-foreground mt-0.5 inline-flex items-center gap-1 font-mono text-xs">
                          <GitCommitIcon className="size-3" />
                          {a.sourceKind === "github" && a.sourceRepo ? (
                            <a
                              href={githubCommitUrl(
                                a.sourceRepo,
                                d.commitSha,
                              )}
                              target="_blank"
                              rel="noreferrer"
                              className="hover:underline"
                              onClick={(e) => e.stopPropagation()}
                            >
                              {d.commitSha.slice(0, 7)}
                            </a>
                          ) : (
                            d.commitSha.slice(0, 7)
                          )}
                          {d.branch && (
                            <span className="text-muted-foreground/80">
                              · {d.branch}
                            </span>
                          )}
                        </div>
                      )}
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline" className="text-xs capitalize">
                        {d.triggerKind}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <Badge variant="secondary" className="capitalize">
                        {d.status.replace(/_/g, " ")}
                      </Badge>
                      {d.approvalsRequired > 0 && (
                        <div className="text-muted-foreground mt-1 text-xs">
                          {d.approvalsReceived}/{d.approvalsRequired} approvals
                        </div>
                      )}
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
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <p className="text-muted-foreground text-center text-xs">
        Showing the latest {allDeployments.length} deployments. Older
        rollouts are accessible via the API — pagination ships once the
        list grows past a single window.{" "}
        <Link
          href={`/apps/${a.slug}/environments`}
          className="hover:text-foreground underline"
        >
          Roll a new deployment
        </Link>
        .
      </p>
    </PageShell>
  );
}
