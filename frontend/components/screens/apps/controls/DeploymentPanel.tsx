"use client";

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

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { DeploymentStatus } from "@/graphql/lifecycle/lifecycle.types";
import { useFormatters } from "@/lib/i18n/formatters";

import type { useDeploymentPanel } from "./use-deployment-panel";

export type DeploymentPanelViewProps = ReturnType<typeof useDeploymentPanel>;

const STATUS_TONE: Record<
  DeploymentStatus,
  { label: string; className: string; icon: React.ComponentType<{ className?: string }> }
> = {
  running: {
    label: "Running",
    className: "bg-success/10 text-success-fg border-success-border",
    icon: CheckCircle2Icon,
  },
  failed: {
    label: "Failed",
    className: "bg-danger/10 text-danger-fg border-danger-border",
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
    className: "bg-warning/10 text-warning-fg border-warning-border",
    icon: Loader2Icon,
  },
  pending: {
    label: "Pending",
    className: "bg-warning/10 text-warning-fg border-warning-border",
    icon: ClockIcon,
  },
  pending_approval: {
    label: "Awaiting approval",
    className: "bg-info/10 text-info-fg border-info-border",
    icon: ClockIcon,
  },
  redeploying: {
    label: "Redeploying",
    className: "bg-warning/10 text-warning-fg border-warning-border",
    icon: Loader2Icon,
  },
};

const IN_PROGRESS = new Set<DeploymentStatus>(["pending", "deploying", "redeploying"]);

/**
 * Live status panel for the most-recent deployment. An in-flight deploy
 * updates without a refresh (the hook subscribes to the lifecycle
 * stream), and a rollback action on the last-known-good surfaces when the
 * current deployment is failing.
 */
export function DeploymentPanelView({
  loading,
  current,
  lastGood,
  rolling,
  onRollback,
}: DeploymentPanelViewProps) {
  const fmt = useFormatters();
  const [confirmRollback, setConfirmRollback] = React.useState(false);

  if (loading) {
    return <Skeleton className="h-32 w-full rounded-lg" />;
  }

  if (!current) {
    return (
      <Card>
        <CardContent className="py-2">
          <div className="text-muted-foreground flex flex-col items-center gap-2 text-center text-sm">
            <RocketIcon className="size-5" />
            <p className="font-medium">No deployments yet</p>
            <p className="text-xs">
              Trigger a deploy from the CLI or push to the deploy branch to see status here.
            </p>
          </div>
        </CardContent>
      </Card>
    );
  }

  const tone = STATUS_TONE[current.status] ?? STATUS_TONE.superseded;
  const Icon = tone.icon;
  const inProgress = IN_PROGRESS.has(current.status);
  const elapsedFrom = current.startedAt ?? current.createdAt;
  const elapsed = elapsedSeconds(elapsedFrom, current.endedAt);

  return (
    <Card>
      <CardContent>
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
                {(current.imageTag || current.id).slice(0, 12)}
              </span>
              {current.environmentName && (
                <Badge variant="secondary" className="text-2xs">
                  {current.environmentName}
                </Badge>
              )}
              {current.workloadSlug && (
                <Badge variant="outline" className="text-2xs font-mono">
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
                <Button
                  onClick={() => setConfirmRollback(true)}
                  disabled={rolling}
                  size="sm"
                  variant="default"
                >
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
          <div className="border-danger-border bg-danger/5 mt-4 rounded-md border p-3 text-xs">
            <p className="text-danger-fg font-medium">Deployment failed</p>
            <p className="text-muted-foreground mt-1">
              Open the deployment for the full log.{" "}
              {lastGood
                ? `Rolling back targets ${(lastGood.imageTag || lastGood.id).slice(0, 10)}.`
                : "No earlier successful deploy to roll back to."}
            </p>
          </div>
        )}
      </CardContent>

      <ConfirmDialog
        open={confirmRollback}
        onOpenChange={setConfirmRollback}
        title={
          lastGood
            ? `Roll back to ${(lastGood.imageTag || lastGood.id).slice(0, 10)}?`
            : "Roll back?"
        }
        description="This starts a new deployment using the previous image and supersedes the failed one."
        confirmLabel="Roll back"
        destructive
        onConfirm={onRollback}
      />
    </Card>
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
      <div className="text-muted-foreground text-2xs mb-1.5 flex items-center justify-between">
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
