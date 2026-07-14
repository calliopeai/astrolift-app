"use client";

import { useQuery } from "@apollo/client/react";
import { RocketIcon } from "lucide-react";
import Link from "next/link";

import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useFormatters } from "@/lib/i18n/formatters";
import { cn } from "@/lib/utils";
import { LIST_DEPLOYMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftDeployment, DeploymentStatus } from "@/graphql/lifecycle/lifecycle.types";

interface DeploymentsResp {
  astroliftDeployments: AstroliftDeployment[];
}

// Five-state color scheme — green for healthy, red for failures, amber for
// in-flight, slate for superseded. Pulse only on still-running rows so the
// strip telegraphs which one the operator is waiting on.
const TILE_TONE: Record<DeploymentStatus, string> = {
  running: "bg-success/85 hover:bg-success border-success-border",
  failed: "bg-danger/85 hover:bg-danger border-danger-border",
  rolled_back: "bg-muted-foreground/40 hover:bg-muted-foreground/60 border-muted-foreground/30",
  superseded: "bg-muted-foreground/30 hover:bg-muted-foreground/50 border-muted-foreground/20",
  deploying: "bg-warning/85 hover:bg-warning border-warning-border animate-pulse",
  pending: "bg-warning/70 hover:bg-warning border-warning-border animate-pulse",
  pending_approval: "bg-purple-500/85 hover:bg-purple-500 border-purple-500/50 animate-pulse",
  redeploying: "bg-warning/85 hover:bg-warning border-warning-border animate-pulse",
};

const STATUS_LABEL: Record<DeploymentStatus, string> = {
  running: "running",
  failed: "failed",
  rolled_back: "rolled back",
  superseded: "superseded",
  deploying: "deploying",
  pending: "pending",
  pending_approval: "awaiting approval",
  redeploying: "redeploying",
};

const IN_FLIGHT = new Set<DeploymentStatus>([
  "pending",
  "pending_approval",
  "deploying",
  "redeploying",
]);

interface Props {
  appSlug: string;
  limit?: number;
}

/**
 * Horizontal heatmap of the last N deploys for an app. Tile color encodes
 * status; hovering surfaces the time + image tag + status. Click jumps to
 * the deployment detail page so an operator can drill into a red square
 * without navigating to the full deployments list first.
 */
export function DeployActivityStrip({ appSlug, limit = 20 }: Props) {
  const fmt = useFormatters();
  const { data, loading } = useQuery<DeploymentsResp>(LIST_DEPLOYMENTS, {
    variables: { appSlug, limit },
    fetchPolicy: "cache-and-network",
    pollInterval: 15_000,
  });

  const deployments = (data?.astroliftDeployments ?? []) as AstroliftDeployment[];
  const inFlight = deployments.filter((d) => IN_FLIGHT.has(d.status));

  // Render the strip newest-on-the-right so the timeline reads naturally
  // and the freshest deploy lines up with the "View all" link.
  const ordered = [...deployments].reverse();

  return (
    <Card size="sm">
      <CardContent>
        <div className="mb-2 flex items-baseline justify-between gap-3">
          <div className="flex items-baseline gap-2">
            <p className="text-muted-foreground text-2xs font-medium tracking-wide uppercase">
              Deploy activity
            </p>
            <p className="text-muted-foreground text-2xs">
              Last {limit}
              {inFlight.length > 0 && (
                <span className="ml-2 text-warning-fg">
                  · {inFlight.length} in flight
                </span>
              )}
            </p>
          </div>
          <Link
            href={`/apps/${appSlug}/environments`}
            className="text-xs text-[var(--brand-primary)] hover:underline"
          >
            View all
          </Link>
        </div>

        {loading && deployments.length === 0 ? (
          <Skeleton className="h-7 w-full" />
        ) : deployments.length === 0 ? (
          <div className="text-muted-foreground flex items-center gap-2 py-1 text-xs italic">
            <RocketIcon className="size-3.5" />
            No deploys yet — trigger one from the CLI or push to the deploy branch.
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-1">
            {ordered.map((d) => (
              <DeployTile
                key={d.id}
                dep={d}
                appSlug={appSlug}
                relativeTime={fmt.formatRelativeTime(d.createdAt)}
              />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function DeployTile({
  dep,
  appSlug,
  relativeTime,
}: {
  dep: AstroliftDeployment;
  appSlug: string;
  relativeTime: string;
}) {
  const tone = TILE_TONE[dep.status] ?? TILE_TONE.superseded;
  const label = STATUS_LABEL[dep.status] ?? dep.status;
  const tag = dep.imageTag ? dep.imageTag.slice(0, 10) : dep.id.slice(0, 8);
  const tooltip = `${tag} · ${label} · ${relativeTime}${
    dep.environmentName ? ` · ${dep.environmentName}` : ""
  }`;

  return (
    <Link
      href={`/apps/${appSlug}/environments?deployment=${dep.id}`}
      title={tooltip}
      aria-label={tooltip}
      className={cn("size-6 shrink-0 rounded-sm border transition-all hover:scale-110", tone)}
    />
  );
}
