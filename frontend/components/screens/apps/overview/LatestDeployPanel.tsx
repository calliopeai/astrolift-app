"use client";

/**
 * The overview's first panel (spec 44 §5.2): the latest deployment, live from
 * the lifecycle stream. A failed deploy puts its reason first, in the panel's
 * danger strip, with Roll back beside it when there is a good deploy to go
 * back to. Approvals waiting on this app sit under it.
 */

import { ExternalLinkIcon, Loader2Icon, RocketIcon, RotateCcwIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Panel, type PanelSpan } from "@/components/panel/Panel";
import { formatElapsed } from "@/components/run/format";
import type { useDeploymentPanel } from "@/components/screens/apps/controls/use-deployment-panel";
import { StatusDot } from "@/components/StatusDot";
import { Button } from "@/components/ui/button";
import type { AstroliftDeployment, DeploymentStatus } from "@/graphql/lifecycle/lifecycle.types";
import { useFormatters } from "@/lib/i18n/formatters";

export type LatestDeployPanelProps = ReturnType<typeof useDeploymentPanel> & {
  /** The app's own base path, e.g. `/apps/acme`. */
  appHref: string;
  /** Deploys waiting on an approver (PendingDeployments); renders nothing when none wait. */
  pending?: React.ReactNode;
  span?: PanelSpan;
};

const DOT: Record<DeploymentStatus, "ok" | "error" | "pending" | "muted"> = {
  running: "ok",
  failed: "error",
  rolled_back: "muted",
  superseded: "muted",
  deploying: "pending",
  pending: "pending",
  pending_approval: "pending",
  redeploying: "pending",
};

const IN_PROGRESS = new Set<DeploymentStatus>(["pending", "deploying", "redeploying"]);

/** What the system said went wrong, most specific first. */
export function deployFailureReason(d: AstroliftDeployment): string {
  return d.statusReason || d.buildError || d.abortedReason || d.manifestResyncError || "";
}

function shortTag(d: Pick<AstroliftDeployment, "imageTag" | "id">): string {
  return (d.imageTag || d.id).slice(0, 12);
}

export function LatestDeployPanel({
  loading,
  current,
  lastGood,
  rolling,
  onRollback,
  appHref,
  pending,
  span = 6,
}: LatestDeployPanelProps) {
  const t = useTranslations("apps.overview.latestDeploy");
  const fmt = useFormatters();
  const [confirmRollback, setConfirmRollback] = React.useState(false);

  const failed = current?.status === "failed";
  const rollback =
    failed && lastGood ? (
      <Can permission="app.rollback">
        <Button size="sm" onClick={() => setConfirmRollback(true)} disabled={rolling}>
          {rolling ? (
            <Loader2Icon className="size-3.5 animate-spin" />
          ) : (
            <RotateCcwIcon className="size-3.5" />
          )}
          {t("rollback")}
        </Button>
      </Can>
    ) : undefined;

  const start = current ? (current.startedAt ?? current.createdAt) : null;
  const inProgress = current ? IN_PROGRESS.has(current.status) : false;
  // An in-flight deploy's elapsed time ticks; a finished one reads its end.
  const [now, setNow] = React.useState<number | null>(null);
  React.useEffect(() => {
    if (!inProgress) return;
    const tick = () => setNow(Date.now());
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [inProgress]);
  const end = current?.endedAt ? new Date(current.endedAt).getTime() : inProgress ? now : null;
  const elapsedMs =
    current?.durationSeconds != null
      ? current.durationSeconds * 1000
      : start && end != null
        ? end - new Date(start).getTime()
        : null;
  const who = current?.commitAuthor?.trim() || current?.ciActorKind?.trim() || "";

  return (
    <Panel
      title={t("title")}
      icon={<RocketIcon className="size-4" />}
      span={span}
      loading={loading}
      actions={
        current ? (
          <Button asChild size="sm" variant="ghost">
            <Link href={`/deployments/${current.id}`}>
              <ExternalLinkIcon className="size-3.5" />
              {t("open")}
            </Link>
          </Button>
        ) : undefined
      }
      failure={
        current && failed
          ? {
              title: t("failed"),
              reason: deployFailureReason(current) || t("noReason"),
              action: rollback,
            }
          : null
      }
      empty={
        current
          ? null
          : {
              icon: <RocketIcon className="size-5" />,
              title: t("emptyTitle"),
              description: t("emptyDescription"),
              actionHref: `${appHref}/deployments`,
              actionLabel: t("emptyAction"),
            }
      }
    >
      {current && (
        <div className="flex min-w-0 flex-col gap-3">
          <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
            <span className="flex items-center gap-1.5 text-sm font-medium">
              <StatusDot status={DOT[current.status] ?? "muted"} />
              {t(`status.${current.status}`)}
            </span>
            <span
              className="min-w-0 font-mono text-sm [overflow-wrap:anywhere]"
              title={current.imageTag || current.id}
            >
              {shortTag(current)}
            </span>
            {current.environmentName && (
              <span className="text-muted-foreground min-w-0 text-xs [overflow-wrap:anywhere]">
                {current.environmentName}
              </span>
            )}
            {current.workloadSlug && (
              <span className="text-muted-foreground min-w-0 font-mono text-xs [overflow-wrap:anywhere]">
                {current.workloadSlug}
              </span>
            )}
          </div>

          <dl className="text-muted-foreground grid min-w-0 grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
            <dt>{t("when")}</dt>
            <dd className="min-w-0">
              <span className="font-mono" title={current.createdAt}>
                {fmt.formatRelativeTime(current.createdAt)}
              </span>
              {elapsedMs != null && (
                <>
                  {" · "}
                  {inProgress ? t("elapsed") : t("took")}{" "}
                  <span className="font-mono">{formatElapsed(elapsedMs)}</span>
                </>
              )}
            </dd>
            {current.commitSha && (
              <>
                <dt>{t("commit")}</dt>
                <dd className="min-w-0 [overflow-wrap:anywhere]">
                  <span className="text-foreground font-mono">{current.commitSha.slice(0, 7)}</span>
                  {current.commitMessage && <> {current.commitMessage.split("\n")[0]}</>}
                </dd>
              </>
            )}
            <dt>{t("trigger")}</dt>
            <dd className="min-w-0 [overflow-wrap:anywhere]">
              {current.triggerKind}
              {who && <> · {who}</>}
            </dd>
          </dl>

          {failed && !lastGood && (
            <p className="text-muted-foreground text-xs">{t("noLastGood")}</p>
          )}

          {pending}

          <Link
            href={`${appHref}/deployments`}
            className="text-primary self-start text-xs hover:underline"
          >
            {t("all")}
          </Link>
        </div>
      )}

      <ConfirmDialog
        open={confirmRollback}
        onOpenChange={setConfirmRollback}
        title={lastGood ? t("confirmTitle", { tag: shortTag(lastGood) }) : t("rollback")}
        description={t("confirmDescription")}
        confirmLabel={t("rollback")}
        destructive
        onConfirm={onRollback}
      />
    </Panel>
  );
}
