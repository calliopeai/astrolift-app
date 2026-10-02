"use client";

import {
  ActivityIcon,
  CheckIcon,
  CopyIcon,
  ExternalLinkIcon,
  FileCodeIcon,
  InfoIcon,
  MoreHorizontalIcon,
  NotebookTextIcon,
  RadioIcon,
  RocketIcon,
  RotateCcwIcon,
  StopCircleIcon,
  Trash2Icon,
  UndoIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DeploymentStatusPill } from "@/components/DeploymentStatusPill";
import { Identifier } from "@/components/Identifier";
import { Panel, PanelGrid } from "@/components/panel/Panel";
import { RunPage } from "@/components/run/RunPage";
import {
  formatDuration,
  formatTime,
} from "@/components/screens/apps/deployments/app-deployments-format";
import { ShellHeader } from "@/components/shell/ShellHeader";
import { StaleManifestNotice } from "@/components/StaleManifestNotice";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DefinitionList } from "@/components/ui/definition-list";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { AstroliftDeployment } from "@/graphql/lifecycle/lifecycle.types";

import { appsDetailCrumbs } from "./apps-area";
import { IN_FLIGHT } from "./deployments-format";
import {
  deploymentFailure,
  deploymentLogLines,
  deploymentSteps,
  deploySha,
} from "./deployments-list";
import type { useDeploymentDetail } from "./use-deployment-detail";

export type DeploymentDetailScreenProps = ReturnType<typeof useDeploymentDetail>;

/** Deployments ▾ › storefront › deploy 4f2a9c1e (spec 44 §4.4). */
function crumbs(d: AstroliftDeployment | null) {
  if (!d) return appsDetailCrumbs("deployments", { label: "deploy …" });
  return appsDetailCrumbs(
    "deployments",
    { label: d.registeredAppSlug, href: `/apps/${d.registeredAppSlug}/deployments` },
    { label: `deploy ${deploySha(d)}` }
  );
}

function elapsedMs(d: AstroliftDeployment, now: number): number | null {
  if (IN_FLIGHT.has(d.status) && d.status !== "pending_approval") {
    const from = Date.parse(d.startedAt ?? d.createdAt);
    return Number.isFinite(from) ? Math.max(0, now - from) : null;
  }
  return d.durationSeconds != null ? d.durationSeconds * 1000 : null;
}

/**
 * One deployment on the run archetype (spec 44 §5.5, #2123): the phases
 * (build, push, approval, rollout, health) on the left, the lifecycle log on
 * the right, status, duration and the next action in the header, and a
 * failure's reason first. Below the run: the deploy's details, approvals,
 * release notes, events and rendered manifest. Pure view; the data half is
 * useDeploymentDetail.
 */
export function DeploymentDetailScreen({
  deployment: d,
  loading,
  error,
  onRetry,
  now,
  log,
  logLoading,
  logError,
  onRetryLog,
  onDownload,
  hasOlderLog,
  onLoadOlderLog,
  manifest,
  manifestLoading,
  events,
  eventsLoading,
  approvalHistory,
  approvalHistoryLoading,
  releaseNotes,
  canApprove,
  canDeploy,
  canRollback,
  busy,
  onApprove,
  onAbort,
  onRollback,
  onRedeploy,
  onDelete,
}: DeploymentDetailScreenProps) {
  const t = useTranslations("lists.deploymentDetail");
  const actionLabel = useTranslations("lists.deployments.actions");
  const [confirmAbort, setConfirmAbort] = React.useState(false);
  const [confirmRollback, setConfirmRollback] = React.useState(false);
  const [confirmDelete, setConfirmDelete] = React.useState(false);

  const lines = React.useMemo(() => deploymentLogLines(log), [log]);

  if (!d && loading) {
    return (
      <RunPage
        crumbs={crumbs(null)}
        title={t("loadingTitle")}
        steps={[]}
        stepsLoading
        log={{ lines: [], loading: true, title: t("lifecycle") }}
      />
    );
  }

  if (!d) {
    return (
      <div className="flex min-w-0 flex-1 flex-col gap-4">
        <ShellHeader crumbs={crumbs(null)} title={error ? t("loadingTitle") : t("notFoundTitle")} />
        <PanelGrid>
          <Panel
            title={t("loadingTitle")}
            icon={<RocketIcon className="size-4" />}
            error={error}
            onRetry={onRetry}
            empty={
              error
                ? null
                : {
                    icon: <RocketIcon className="size-5" />,
                    title: t("notFoundTitle"),
                    description: t("notFoundDescription"),
                    actionHref: "/deployments",
                    actionLabel: "Open deployments",
                  }
            }
          />
        </PanelGrid>
      </div>
    );
  }

  const inFlight = IN_FLIGHT.has(d.status);
  const canApproveThis =
    d.status === "pending_approval" && d.approvalsReceived < d.approvalsRequired && canApprove;
  const canAbortThis = inFlight && canDeploy;
  const canRollbackThis = d.status === "running" && canRollback;
  const canRedeployThis = (d.status === "running" || d.status === "failed") && canDeploy;
  // Terminal rows soft-delete; a running row is retired (superseded).
  // In-flight rows abort first.
  const canDeleteThis =
    (d.status === "failed" ||
      d.status === "superseded" ||
      d.status === "rolled_back" ||
      d.status === "running") &&
    canDeploy;

  // The one next action (spec 44 §4.4 rule 4); the rest sit in ⋯.
  const primaryAction = canApproveThis ? (
    <Can permission="app.approve_deploy">
      <Button size="sm" disabled={busy} onClick={onApprove}>
        <CheckIcon className="size-4" /> {t("approve")}
      </Button>
    </Can>
  ) : canAbortThis ? (
    <Can permission="app.deploy">
      <Button size="sm" variant="destructive" disabled={busy} onClick={() => setConfirmAbort(true)}>
        <StopCircleIcon className="size-4" /> {t("abort")}
      </Button>
    </Can>
  ) : d.status === "failed" && canRedeployThis ? (
    <Can permission="app.deploy">
      <Button size="sm" disabled={busy} onClick={onRedeploy}>
        <RotateCcwIcon className="size-4" /> {t("redeploy")}
      </Button>
    </Can>
  ) : canRollbackThis ? (
    <Can permission="app.rollback">
      <Button size="sm" variant="outline" disabled={busy} onClick={() => setConfirmRollback(true)}>
        <UndoIcon className="size-4" /> {t("rollback")}
      </Button>
    </Can>
  ) : null;

  const commitHref =
    d.repoUrl && d.commitSha ? `${d.repoUrl.replace(/\/$/, "")}/commit/${d.commitSha}` : null;

  const menu = (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="icon" className="size-8" aria-label={actionLabel("more")}>
          <MoreHorizontalIcon className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-48">
        {d.status === "running" && canRedeployThis && (
          <DropdownMenuItem disabled={busy} onSelect={() => void onRedeploy()}>
            <RotateCcwIcon className="size-4" />
            {t("redeploy")}
          </DropdownMenuItem>
        )}
        {commitHref && (
          <DropdownMenuItem asChild>
            <a href={commitHref} target="_blank" rel="noopener noreferrer">
              <ExternalLinkIcon className="size-4" />
              {actionLabel("viewCommit")}
            </a>
          </DropdownMenuItem>
        )}
        {d.ciRunUrl && (
          <DropdownMenuItem asChild>
            <a href={d.ciRunUrl} target="_blank" rel="noopener noreferrer">
              <ExternalLinkIcon className="size-4" />
              {t("openCiRun")}
            </a>
          </DropdownMenuItem>
        )}
        <DropdownMenuItem
          onSelect={() => {
            navigator.clipboard
              .writeText(d.id)
              .then(() => toast.success(actionLabel("copyOk")))
              .catch(() => toast.error(actionLabel("copyFail")));
          }}
        >
          <CopyIcon className="size-4" />
          {actionLabel("copyGuid")}
        </DropdownMenuItem>
        {canDeleteThis && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              variant="destructive"
              disabled={busy}
              onSelect={() => setConfirmDelete(true)}
            >
              <Trash2Icon className="size-4" />
              {d.status === "running" ? t("retire") : t("delete")}
            </DropdownMenuItem>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );

  const nonMerge = releaseNotes?.commits.filter((c) => !c.isMerge) ?? [];
  const appEvents = events;

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6">
      <RunPage
        crumbs={crumbs(d)}
        title={`deploy ${deploySha(d)}`}
        status={<DeploymentStatusPill status={d.status} />}
        durationMs={elapsedMs(d, now)}
        context={
          <>
            <span className="font-mono">{d.registeredAppSlug}</span>
            {" · "}
            <span className="font-mono">{d.environmentName}</span>
            {" · "}
            <span className="font-mono">{d.triggerKind}</span>
          </>
        }
        primaryAction={primaryAction}
        menu={menu}
        steps={deploymentSteps(d, log, now)}
        stepsLoading={logLoading && log.length === 0}
        stepsError={logError}
        onRetrySteps={onRetryLog}
        failure={deploymentFailure(d, log)}
        log={{
          lines,
          title: t("lifecycle"),
          onDownload: log.length > 0 ? onDownload : undefined,
          loading: logLoading && log.length === 0,
          error: logError,
          onRetry: onRetryLog,
          emptyHint: t("noLog"),
        }}
      />

      {hasOlderLog && (
        <Button
          variant="outline"
          disabled={logLoading}
          onClick={() => {
            void onLoadOlderLog();
          }}
        >
          {t("olderLog")}
        </Button>
      )}

      {/* #1553: this rollout rendered the stored manifest, not the repo's.
          It changes what a green deploy means, so it leads the details. */}
      <StaleManifestNotice
        status={d.manifestResyncStatus}
        error={d.manifestResyncError}
        appSlug={d.registeredAppSlug}
      />

      <PanelGrid className="items-start">
        <Panel title="Details" icon={<InfoIcon className="size-4" />} span={6}>
          <DefinitionList
            items={[
              {
                term: t("fields.imageTag"),
                description: d.imageTag ? <Identifier value={d.imageTag} form="full" /> : "—",
              },
              {
                term: t("fields.imageDigest"),
                description: d.imageDigest ? (
                  <Identifier value={d.imageDigest} kind="digest" form="full" />
                ) : (
                  "—"
                ),
              },
              {
                term: t("fields.commit"),
                description: d.commitSha ? (
                  <Identifier value={d.commitSha} kind="sha" form="full" />
                ) : (
                  "—"
                ),
              },
              {
                term: t("fields.branch"),
                description: <span className="font-mono text-xs">{d.branch || "—"}</span>,
              },
              {
                term: t("fields.workload"),
                description: <span className="font-mono text-xs">{d.workloadSlug || "—"}</span>,
              },
              {
                term: t("fields.clusterRevision"),
                description: <span className="font-mono text-xs">{d.clusterRevision || "—"}</span>,
              },
              {
                term: "Strategy",
                description: (
                  <span className="font-mono text-xs">
                    {d.strategy && d.strategy !== "unknown" ? d.strategy.replace(/_/g, " ") : "—"}
                  </span>
                ),
              },
              {
                term: t("fields.ciProvider"),
                description: (
                  <span className="font-mono text-xs">
                    {[d.ciProvider, d.ciActorKind].filter(Boolean).join(" · ") || "—"}
                  </span>
                ),
              },
              {
                term: t("fields.ciRun"),
                description: d.ciRunUrl ? (
                  <a
                    href={d.ciRunUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="font-mono text-xs [overflow-wrap:anywhere] hover:underline"
                  >
                    {d.ciRunUrl}
                  </a>
                ) : (
                  "—"
                ),
              },
              {
                term: t("fields.created"),
                description: <span className="font-mono text-xs">{formatTime(d.createdAt)}</span>,
              },
              {
                term: t("fields.started"),
                description: <span className="font-mono text-xs">{formatTime(d.startedAt)}</span>,
              },
              {
                term: d.failedAt ? t("fields.failed") : t("fields.succeeded"),
                description: (
                  <span className="font-mono text-xs">
                    {formatTime(d.failedAt ?? d.succeededAt)}
                  </span>
                ),
              },
              {
                term: t("fields.duration"),
                description: (
                  <span className="font-mono text-xs">{formatDuration(d.durationSeconds)}</span>
                ),
              },
            ]}
          />
        </Panel>

        {/* #653: the approval trail when the deploy gated through quorum, else
            who or what triggered it. */}
        <Panel
          title="Approvals"
          icon={<ActivityIcon className="size-4" />}
          span={6}
          loading={approvalHistoryLoading}
          actions={
            d.approvalsRequired > 0 ? (
              <Badge variant="secondary" className="font-mono">
                {t("approvalsCount", {
                  received: d.approvalsReceived,
                  required: d.approvalsRequired,
                })}
              </Badge>
            ) : undefined
          }
        >
          {approvalHistory.length === 0 ? (
            <p className="text-muted-foreground text-sm [overflow-wrap:anywhere]">
              {d.triggeredByUserId
                ? `Triggered by user ${d.triggeredByUserId} via ${d.triggerKind}.`
                : `Triggered automatically via ${d.triggerKind}${d.ciProvider ? ` (${d.ciProvider})` : ""}.`}
              {d.approvalsRequired === 0 && " No approvals required for this environment."}
            </p>
          ) : (
            <ol className="flex min-w-0 flex-col divide-y text-sm">
              {approvalHistory.map((e) => (
                <li key={e.id} className="min-w-0 py-2 first:pt-0 last:pb-0">
                  <div className="flex min-w-0 flex-wrap items-baseline gap-2">
                    <span className="font-medium capitalize">{e.action}</span>
                    {e.decision && e.decision !== "none" && (
                      <Badge
                        variant={
                          e.decision === "approved"
                            ? "default"
                            : e.decision === "rejected"
                              ? "destructive"
                              : "outline"
                        }
                        className="text-2xs capitalize"
                      >
                        {e.decision}
                      </Badge>
                    )}
                    <span className="text-muted-foreground min-w-0 font-mono text-xs [overflow-wrap:anywhere]">
                      {e.actorDisplay} ({e.actorKind})
                    </span>
                    <span className="text-muted-foreground ml-auto font-mono text-xs">
                      {formatTime(e.occurredAt)}
                    </span>
                  </div>
                  {e.reason && (
                    <p className="text-muted-foreground mt-0.5 [overflow-wrap:anywhere] italic">
                      {e.reason}
                    </p>
                  )}
                </li>
              ))}
            </ol>
          )}
        </Panel>

        {/* #657, #738: the PRs and commits since the previous good deploy,
            else the raw commit message. */}
        {(releaseNotes || d.commitMessage) && (
          <Panel title="Release notes" icon={<NotebookTextIcon className="size-4" />} span={6}>
            {releaseNotes ? (
              <div className="flex min-w-0 flex-col gap-3 text-sm">
                {releaseNotes.pullRequests.length > 0 && (
                  <ul className="flex min-w-0 flex-col gap-2">
                    {releaseNotes.pullRequests.map((pr) => (
                      <li key={pr.number} className="border-muted min-w-0 border-l-2 pl-3">
                        <span className="font-medium [overflow-wrap:anywhere]">{pr.title}</span>
                        {pr.prUrl && (
                          <a
                            href={pr.prUrl}
                            target="_blank"
                            rel="noreferrer"
                            className="text-muted-foreground ml-2 font-mono text-xs hover:underline"
                          >
                            #{pr.number}
                          </a>
                        )}
                        {pr.body && (
                          <p className="text-muted-foreground mt-1 text-xs [overflow-wrap:anywhere] whitespace-pre-wrap">
                            {pr.body.slice(0, 400)}
                            {pr.body.length > 400 && "…"}
                          </p>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
                {nonMerge.length > 0 && (
                  <details>
                    <summary className="text-muted-foreground cursor-pointer text-xs">
                      <span className="font-mono">{nonMerge.length}</span> commits
                    </summary>
                    <ul className="mt-2 flex flex-col gap-1">
                      {nonMerge.map((c) => (
                        <li
                          key={c.sha}
                          className="text-muted-foreground font-mono text-xs [overflow-wrap:anywhere]"
                        >
                          <span className="text-foreground">{c.sha.slice(0, 7)}</span> {c.subject}
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
                {releaseNotes.compareUrl && (
                  <a
                    href={releaseNotes.compareUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="text-muted-foreground text-xs hover:underline"
                  >
                    View full diff
                  </a>
                )}
              </div>
            ) : (
              <details>
                <summary className="text-muted-foreground cursor-pointer text-xs [overflow-wrap:anywhere]">
                  {d.commitMessage.split("\n")[0].slice(0, 120)}
                  {d.commitMessage.length > 120 && "…"}
                </summary>
                <pre className="bg-muted mt-2 max-h-64 overflow-auto rounded p-3 font-mono text-xs [overflow-wrap:anywhere] whitespace-pre-wrap">
                  {d.commitMessage}
                </pre>
              </details>
            )}
          </Panel>
        )}

        <Panel
          title={t("events")}
          icon={<RadioIcon className="size-4" />}
          span={6}
          loading={eventsLoading}
          empty={
            appEvents.length === 0
              ? { icon: <RadioIcon className="size-5" />, title: t("noEvents") }
              : null
          }
          flush
        >
          <ul className="divide-y">
            {appEvents.slice(0, 12).map((e) => (
              <li key={e.id} className="flex min-w-0 items-center justify-between gap-2 px-4 py-2">
                <span className="min-w-0 truncate font-mono text-xs">{e.eventType}</span>
                <span className="text-muted-foreground shrink-0 font-mono text-xs">
                  {formatTime(e.occurredAt)}
                </span>
              </li>
            ))}
          </ul>
        </Panel>

        <Panel
          title={t("manifests")}
          icon={<FileCodeIcon className="size-4" />}
          span={12}
          loading={manifestLoading}
          failure={
            manifest?.error
              ? {
                  title: t("renderFailed"),
                  reason: (
                    <>
                      {manifest.error}
                      {manifest.errorPath && (
                        <span className="block">
                          {manifest.errorPath}
                          {manifest.errorLine != null && `:${manifest.errorLine}`}
                        </span>
                      )}
                    </>
                  ),
                }
              : null
          }
          empty={
            !manifest
              ? { icon: <FileCodeIcon className="size-5" />, title: t("noManifests") }
              : null
          }
        >
          {manifest && !manifest.error && (
            <pre className="bg-muted max-h-96 overflow-auto rounded p-3 font-mono text-xs leading-relaxed">
              {JSON.stringify(manifest.resources, null, 2)}
            </pre>
          )}
        </Panel>
      </PanelGrid>

      <ConfirmDialog
        reason={{
          label: t("confirmAbort.reasonLabel"),
          placeholder: t("confirmAbort.reasonPlaceholder"),
          requiredError: t("confirmAbort.reasonRequired"),
        }}
        open={confirmAbort}
        onOpenChange={setConfirmAbort}
        title={t("confirmAbort.title", { app: d.registeredAppSlug, env: d.environmentName })}
        description={t("confirmAbort.description")}
        confirmLabel={t("confirmAbort.confirm")}
        destructive
        onConfirm={onAbort}
      />

      <ConfirmDialog
        open={confirmRollback}
        onOpenChange={setConfirmRollback}
        title={t("confirmRollback.title", { app: d.registeredAppSlug, env: d.environmentName })}
        description={t("confirmRollback.description")}
        confirmLabel={t("confirmRollback.confirm")}
        onConfirm={onRollback}
      />

      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title={
          d.status === "running"
            ? t("confirmDelete.retireTitle", { app: d.registeredAppSlug, env: d.environmentName })
            : t("confirmDelete.title", { app: d.registeredAppSlug, env: d.environmentName })
        }
        description={
          d.status === "running"
            ? t("confirmDelete.retireDescription")
            : t("confirmDelete.description")
        }
        confirmLabel={
          d.status === "running" ? t("confirmDelete.retireConfirm") : t("confirmDelete.confirm")
        }
        destructive
        onConfirm={onDelete}
      />
    </div>
  );
}
