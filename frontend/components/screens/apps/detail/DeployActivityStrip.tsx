"use client";

import { RocketIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { Skeleton } from "@/components/ui/skeleton";
import { useFormatters } from "@/lib/i18n/formatters";
import { cn } from "@/lib/utils";
import type { AstroliftDeployment, DeploymentStatus } from "@/graphql/lifecycle/lifecycle.types";

import type { useDeployActivity } from "./use-deploy-activity";

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
  pending_approval: "bg-info/85 hover:bg-info border-info-border animate-pulse",
  redeploying: "bg-warning/85 hover:bg-warning border-warning-border animate-pulse",
};

const IN_FLIGHT = new Set<DeploymentStatus>([
  "pending",
  "pending_approval",
  "deploying",
  "redeploying",
]);

export type DeployActivityStripProps = ReturnType<typeof useDeployActivity> & {
  /** The app's own base path, e.g. `/apps/acme` (or `/agents/acme`). */
  appHref: string;
};

/**
 * Horizontal heatmap of the last N deploys for an app, drawn inside the
 * overview's Activity panel. Tile color encodes
 * status; hovering surfaces the time + image tag + status. Click jumps to
 * the deployment detail page so an operator can drill into a red square
 * without navigating to the full deployments list first.
 */
export function DeployActivityStrip({
  appHref,
  deployments,
  loading,
  limit,
}: DeployActivityStripProps) {
  const fmt = useFormatters();
  const t = useTranslations("apps.detail");
  const deploymentsHref = `${appHref}/deployments`;
  const inFlight = deployments.filter((d) => IN_FLIGHT.has(d.status));

  // Render the strip newest-on-the-right so the timeline reads naturally
  // and the freshest deploy lines up with the "View all" link.
  const ordered = [...deployments].reverse();

  return (
    <div className="min-w-0">
      <div className="mb-2 flex min-w-0 flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <div className="flex min-w-0 flex-wrap items-baseline gap-2">
          <p className="text-muted-foreground text-2xs font-medium tracking-wide uppercase">
            {t("deployActivity.title")}
          </p>
          <p className="text-muted-foreground text-2xs">
            {t.rich("deployActivity.latest", {
              limit,
              mono: (chunks) => <span className="font-mono">{chunks}</span>,
            })}
            {inFlight.length > 0 && (
              <span className="text-warning-fg ml-2">
                {t.rich("deployActivity.inFlight", {
                  count: inFlight.length,
                  mono: (chunks) => <span className="font-mono">{chunks}</span>,
                })}
              </span>
            )}
          </p>
        </div>
        <Link href={deploymentsHref} className="text-primary text-xs hover:underline">
          {t("deployActivity.all")}
        </Link>
      </div>

      {loading && deployments.length === 0 ? (
        <Skeleton className="h-7 w-full" />
      ) : deployments.length === 0 ? (
        <div className="text-muted-foreground flex items-center gap-2 py-1 text-xs">
          <RocketIcon className="size-3.5" />
          {t("latestDeploy.empty")}
        </div>
      ) : (
        <div className="flex min-w-0 flex-wrap items-center gap-1">
          {ordered.map((d) => (
            <DeployTile
              key={d.id}
              dep={d}
              deploymentsHref={deploymentsHref}
              relativeTime={fmt.formatRelativeTime(d.createdAt)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function DeployTile({
  dep,
  deploymentsHref,
  relativeTime,
}: {
  dep: AstroliftDeployment;
  deploymentsHref: string;
  relativeTime: string;
}) {
  const tone = TILE_TONE[dep.status] ?? TILE_TONE.superseded;
  const t = useTranslations("apps.overview.latestDeploy.status");
  const label = t.has(dep.status) ? t(dep.status) : dep.status;
  const tag = dep.imageTag ? dep.imageTag.slice(0, 10) : dep.id.slice(0, 8);
  const tooltip = `${tag} · ${label} · ${relativeTime}${
    dep.environmentName ? ` · ${dep.environmentName}` : ""
  }`;

  return (
    <Link
      href={`${deploymentsHref}?deployment=${dep.id}`}
      title={tooltip}
      aria-label={tooltip}
      className={cn("size-6 shrink-0 rounded-sm border transition-all hover:scale-110", tone)}
    />
  );
}
