"use client";

/**
 * LatestDeploymentRow (#407 D) — compact inline row above the
 * quick-link grid showing the most recent deployment at a glance:
 * status pill + short commit + relative time + who triggered.
 *
 * Click anywhere on the row jumps to the deployment detail surface
 * via the same `?deployment=` query param the deploy-activity strip
 * uses, so the cache the strip warmed is reused.
 *
 * Re-uses the existing `LIST_DEPLOYMENTS` query (top deploy only —
 * we slice on the FE). No backend change required for this scope.
 */

import { useQuery } from "@apollo/client/react";
import { ChevronRightIcon, RocketIcon, UserRoundIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { LIST_DEPLOYMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftDeployment, DeploymentStatus } from "@/graphql/lifecycle/lifecycle.types";
import { useFormatters } from "@/lib/i18n/formatters";
import { cn } from "@/lib/utils";

interface DeploymentsResp {
  astroliftDeployments: AstroliftDeployment[];
}

interface Props {
  appSlug: string;
}

const STATUS_TONE: Record<DeploymentStatus, string> = {
  running:
    "border-success-border bg-success/10 text-success-fg",
  failed:
    "border-danger-border bg-danger/10 text-danger-fg",
  rolled_back: "border-muted-foreground/30 bg-muted text-muted-foreground",
  superseded: "border-muted-foreground/20 bg-muted text-muted-foreground",
  deploying:
    "border-warning-border bg-warning/10 text-warning-fg",
  pending:
    "border-warning-border bg-warning/10 text-warning-fg",
  pending_approval:
    "border-purple-300 bg-purple-50 text-purple-700 dark:border-purple-700/50 dark:bg-purple-950/30 dark:text-purple-300",
  redeploying:
    "border-warning-border bg-warning/10 text-warning-fg",
};

export function LatestDeploymentRow({ appSlug }: Props) {
  const t = useTranslations("apps.detail.latestDeploy");
  const fmt = useFormatters();
  // limit=1: we only need the freshest row. The full count powers the
  // QuickLinksGrid's deployments chip via a separate cached query.
  const { data, loading } = useQuery<DeploymentsResp>(LIST_DEPLOYMENTS, {
    variables: { appSlug, limit: 1 },
    fetchPolicy: "cache-and-network",
    pollInterval: 30_000,
  });

  if (loading && !data) {
    return <Skeleton className="h-12 w-full" />;
  }

  const latest = data?.astroliftDeployments?.[0];
  if (!latest) {
    return (
      <div className="text-muted-foreground flex items-center gap-2 rounded-md border border-dashed p-3 text-xs italic">
        <RocketIcon className="size-3.5" />
        {t("empty")}
      </div>
    );
  }

  const tone = STATUS_TONE[latest.status] ?? STATUS_TONE.superseded;
  const shortSha = latest.commitSha ? latest.commitSha.slice(0, 7) : "";
  const triggeredBy =
    latest.commitAuthor?.trim() || latest.ciActorKind?.trim() || latest.triggerKind;
  const relativeTime = fmt.formatRelativeTime(latest.createdAt);

  return (
    <Link
      href={`/apps/${appSlug}/deployments?deployment=${latest.id}`}
      className={cn(
        "group hover:border-primary/40 hover:bg-accent/30",
        "focus-visible:ring-ring/50 rounded-md border transition-colors outline-none focus-visible:ring-3"
      )}
      aria-label={t("ariaLabel", { status: latest.status, when: relativeTime })}
    >
      <div className="flex flex-wrap items-center gap-3 p-3">
        <RocketIcon aria-hidden className="text-muted-foreground size-4 shrink-0" />
        <Badge variant="outline" className={cn("gap-1 font-mono text-2xs uppercase", tone)}>
          {latest.status}
        </Badge>
        {shortSha ? <span className="text-foreground font-mono text-xs">{shortSha}</span> : null}
        {latest.environmentName ? (
          <span className="text-muted-foreground text-xs">· {latest.environmentName}</span>
        ) : null}
        <span className="text-muted-foreground text-xs">· {relativeTime}</span>
        {triggeredBy ? (
          <span className="text-muted-foreground inline-flex items-center gap-1 text-xs">
            <UserRoundIcon className="size-3" />
            <span className="truncate">{triggeredBy}</span>
          </span>
        ) : null}
        <span className="ml-auto" />
        <ChevronRightIcon
          aria-hidden
          className="text-muted-foreground size-4 shrink-0 opacity-0 transition-all group-hover:translate-x-0.5 group-hover:opacity-100"
        />
      </div>
    </Link>
  );
}
