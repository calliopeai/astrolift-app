"use client";

import { useMutation, useQuery, useSubscription } from "@apollo/client/react";
import {
  CheckCircle2Icon,
  ClockIcon,
  ExternalLinkIcon,
  Loader2Icon,
  RocketIcon,
  RotateCcwIcon,
  XCircleIcon,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Can } from "@/components/Can";
import { useFormatters } from "@/lib/i18n/formatters";
import { ROLLBACK_DEPLOYMENT } from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_DEPLOYMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import { DEPLOYMENT_LIFECYCLE_STREAM } from "@/graphql/lifecycle/lifecycle.subscriptions";
import type { AstroliftDeployment, DeploymentStatus } from "@/graphql/lifecycle/lifecycle.types";
import type { MutationResult } from "@/graphql/identity/identity.types";

interface DeploymentsResp {
  astroliftDeployments: AstroliftDeployment[];
}

interface RollbackResp {
  rollbackDeployment: MutationResult<AstroliftDeployment>;
}

const STATUS_TONE: Record<
  DeploymentStatus,
  { label: string; className: string; icon: React.ComponentType<{ className?: string }> }
> = {
  running: {
    label: "Running",
    className: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/30",
    icon: CheckCircle2Icon,
  },
  failed: {
    label: "Failed",
    className: "bg-red-500/10 text-red-600 dark:text-red-400 border-red-500/30",
    icon: XCircleIcon,
  },
  rolled_back: {
    label: "Rolled back",
    className: "bg-muted text-muted-foreground border-muted-foreground/30",
    icon: RotateCcwIcon,
  },
  superseded: {
    label: "Superseded",
    className: "bg-muted text-muted-foreground border-muted-foreground/30",
    icon: ClockIcon,
  },
  deploying: {
    label: "Deploying",
    className: "bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/30",
    icon: Loader2Icon,
  },
  pending: {
    label: "Pending",
    className: "bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/30",
    icon: ClockIcon,
  },
  pending_approval: {
    label: "Awaiting approval",
    className: "bg-purple-500/10 text-purple-600 dark:text-purple-400 border-purple-500/30",
    icon: ClockIcon,
  },
  redeploying: {
    label: "Redeploying",
    className: "bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/30",
    icon: Loader2Icon,
  },
};

const IN_PROGRESS = new Set<DeploymentStatus>(["pending", "deploying", "redeploying"]);

interface Props {
  appSlug: string;
}

/**
 * Live status panel for the most-recent deployment. Subscribes to the
 * lifecycle stream so an in-flight deploy updates without a refresh, and
 * surfaces a rollback action on the last-known-good when the current
 * deployment is failing. Falls back to the recent-deployments list when
 * the subscription isn't carrying a payload yet.
 */
export function DeploymentPanel({ appSlug }: Props) {
  const fmt = useFormatters();
  const { data, loading, refetch } = useQuery<DeploymentsResp>(LIST_DEPLOYMENTS, {
    variables: { appSlug, limit: 10 },
    fetchPolicy: "cache-and-network",
  });

  // Refetch on any lifecycle event for this app — the row deltas come
  // through the same query so the rollback button stays accurate.
  useSubscription(DEPLOYMENT_LIFECYCLE_STREAM, {
    variables: { appSlug },
    onData: () => {
      void refetch();
    },
  });

  const deployments = React.useMemo(() => data?.astroliftDeployments ?? [], [data]);
  const current = deployments[0] ?? null;
  // For rollback, target the last `running` deploy that isn't the current
  // one — that's the version we'd actually flip traffic back to.
  const lastGood = React.useMemo(
    () => deployments.slice(1).find((d) => d.status === "running" || d.status === "rolled_back"),
    [deployments]
  );

  const [rollback, { loading: rolling }] = useMutation<RollbackResp>(ROLLBACK_DEPLOYMENT, {
    refetchQueries: [{ query: LIST_DEPLOYMENTS, variables: { appSlug, limit: 10 } }],
    awaitRefetchQueries: true,
  });

  async function handleRollback() {
    if (!lastGood) return;
    if (
      !confirm(
        `Roll back to ${(lastGood.imageTag ?? lastGood.id).slice(0, 10)}? ` +
          "This starts a new deployment using the previous image and supersedes the failed one."
      )
    ) {
      return;
    }
    const { data } = await rollback({ variables: { input: { id: lastGood.id } } });
    if (data?.rollbackDeployment.ok) {
      toast.success("Rollback started.");
    } else {
      toast.error(data?.rollbackDeployment.errors?.[0]?.message ?? "Rollback failed.");
    }
  }

  if (loading && deployments.length === 0) {
    return <Skeleton className="h-32 w-full rounded-lg" />;
  }

  if (!current) {
    return (
      <section className="rounded-lg border p-6">
        <div className="text-muted-foreground flex flex-col items-center gap-2 text-center text-sm">
          <RocketIcon className="size-5" />
          <p className="font-medium">No deployments yet</p>
          <p className="text-xs">
            Trigger a deploy from the CLI or push to the deploy branch to see status here.
          </p>
        </div>
      </section>
    );
  }

  const tone = STATUS_TONE[current.status] ?? STATUS_TONE.superseded;
  const Icon = tone.icon;
  const inProgress = IN_PROGRESS.has(current.status);
  const elapsedFrom = current.startedAt ?? current.createdAt;
  const elapsed = elapsedSeconds(elapsedFrom, current.endedAt);

  return (
    <section className="rounded-lg border p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-muted-foreground text-2xs font-medium tracking-wide uppercase">
            Current deployment
          </p>
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <Badge variant="outline" className={tone.className}>
              <Icon className={inProgress ? "size-3 animate-spin" : "size-3"} />
              {tone.label}
            </Badge>
            <span className="font-mono text-sm">
              {(current.imageTag ?? current.id).slice(0, 12)}
            </span>
            {current.environmentName && (
              <Badge variant="secondary" className="text-2xs">
                {current.environmentName}
              </Badge>
            )}
            {current.workloadSlug && (
              <Badge variant="outline" className="font-mono text-2xs">
                {current.workloadSlug}
              </Badge>
            )}
          </div>
          <p className="text-muted-foreground mt-1.5 text-xs">
            {fmt.formatRelativeTime(current.createdAt)}
            {elapsed && (
              <>
                {" · "}
                {inProgress ? "elapsed " : "took "}
                {elapsed}
              </>
            )}
            {current.triggerKind && (
              <>
                {" · trigger: "}
                <span className="capitalize">{current.triggerKind}</span>
              </>
            )}
          </p>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <Button asChild variant="outline" size="sm">
            <Link href={`/deployments/${current.id}`}>
              <ExternalLinkIcon className="size-3.5" />
              Open
            </Link>
          </Button>
          {current.status === "failed" && lastGood && (
            <Can permission="app.rollback">
              <Button onClick={handleRollback} disabled={rolling} size="sm" variant="default">
                {rolling ? (
                  <Loader2Icon className="size-3.5 animate-spin" />
                ) : (
                  <RotateCcwIcon className="size-3.5" />
                )}
                Roll back
              </Button>
            </Can>
          )}
        </div>
      </div>

      {inProgress && (
        <ProgressBar status={current.status} startedAt={current.startedAt ?? current.createdAt} />
      )}

      {current.status === "failed" && (
        <div className="mt-4 rounded-md border border-red-500/30 bg-red-500/5 p-3 text-xs">
          <p className="font-medium text-red-700 dark:text-red-400">Deployment failed</p>
          <p className="text-muted-foreground mt-1">
            Open the deployment for the full log.{" "}
            {lastGood
              ? `Rolling back targets ${(lastGood.imageTag ?? lastGood.id).slice(0, 10)}.`
              : "No earlier successful deploy to roll back to."}
          </p>
        </div>
      )}
    </section>
  );
}

function elapsedSeconds(start: string | null | undefined, end: string | null | undefined): string {
  if (!start) return "";
  const startMs = new Date(start).getTime();
  const endMs = end ? new Date(end).getTime() : Date.now();
  const s = Math.max(0, Math.round((endMs - startMs) / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  if (m < 60) return rem === 0 ? `${m}m` : `${m}m ${rem}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

function ProgressBar({ status, startedAt }: { status: DeploymentStatus; startedAt: string }) {
  // No deterministic progress signal from the backend yet — show an
  // animated indeterminate bar so it's clear the deploy is alive, plus
  // a current-stage hint derived from status.
  const stage =
    status === "pending"
      ? "Queued"
      : status === "deploying"
        ? "Rolling out"
        : status === "redeploying"
          ? "Redeploying"
          : "Working";

  return (
    <div className="mt-4">
      <div className="text-muted-foreground mb-1.5 flex items-center justify-between text-2xs">
        <span>{stage}</span>
        <span className="font-mono">
          since {new Date(startedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
        </span>
      </div>
      <div className="bg-muted h-1.5 overflow-hidden rounded-full">
        <div className="h-full w-1/3 animate-[deployBar_1.6s_ease-in-out_infinite] rounded-full bg-[var(--brand-primary)]" />
      </div>
      <style jsx>{`
        @keyframes deployBar {
          0% {
            transform: translateX(-100%);
          }
          100% {
            transform: translateX(300%);
          }
        }
      `}</style>
    </div>
  );
}
