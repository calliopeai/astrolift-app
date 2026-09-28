"use client";

import {
  CheckIcon,
  ClockIcon,
  GripVerticalIcon,
  RotateCcwIcon,
  StopCircleIcon,
  Trash2Icon,
  UndoIcon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";
import {
  Group as PanelGroup,
  Panel,
  Separator as PanelResizeHandle,
  useDefaultLayout,
} from "react-resizable-panels";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DeploymentStatusPill } from "@/components/DeploymentStatusPill";
import { PageShell } from "@/components/PageShell";
import {
  formatDuration,
  formatTime,
} from "@/components/screens/apps/deployments/app-deployments-format";
import { StaleManifestNotice } from "@/components/StaleManifestNotice";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { PipelineDag, type PipelineDagStage } from "@/components/viz";
import type { DeploymentStatus } from "@/graphql/lifecycle/lifecycle.types";
import { useIsMobile } from "@/hooks/use-mobile";

import { IN_FLIGHT, statusToDot } from "./deployments-format";
import type { useDeploymentDetail } from "./use-deployment-detail";

export type DeploymentDetailScreenProps = ReturnType<typeof useDeploymentDetail>;

// useDefaultLayout's storage option defaults to bare `localStorage`, which
// throws during SSR (client components still server-render). Guard both
// sides like AstroliftNav's collapsed-state helpers — degrade silently
// when localStorage is unavailable or disabled.
const splitLayoutStorage = {
  getItem(key: string): string | null {
    if (typeof window === "undefined") return null;
    try {
      return window.localStorage.getItem(key);
    } catch {
      return null;
    }
  },
  setItem(key: string, value: string) {
    if (typeof window === "undefined") return;
    try {
      window.localStorage.setItem(key, value);
    } catch {
      // localStorage might be disabled — degrade silently.
    }
  },
};

/** One deployment: status, fields, flow DAG, lifecycle, release notes, activity, events, manifests. */
export function DeploymentDetailScreen({
  deployment: d,
  loading,
  log,
  logLoading,
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

  // Split-pane plumbing (#1056b): stacked below md, resizable side-by-side
  // at md+. All data hooks live in useDeploymentDetail, above this view,
  // so switching layouts never remounts the lifecycle subscription or any
  // query.
  const isMobile = useIsMobile();
  const splitLayout = useDefaultLayout({
    id: "deployment-detail-split",
    storage: splitLayoutStorage,
  });

  // #1055 — the lifecycle log rendered as a DAG: a linear chain of status
  // transitions. Every entry before the newest reads as completed; the
  // newest carries the live status (in-flight statuses map to "running"
  // so the node pulses).
  const dagStages = React.useMemo<PipelineDagStage[]>(
    () =>
      log.map((e, i) => {
        const last = i === log.length - 1;
        const active = IN_FLIGHT.has(e.status as DeploymentStatus) || e.status === "running";
        return {
          id: e.id,
          name: e.status.replace(/_/g, " "),
          status: !last ? "completed" : active ? "running" : e.status,
          needs: i > 0 ? [log[i - 1].id] : [],
          // Last node: show a live "running" duration while in flight;
          // terminal nodes get no duration (an instant, not a span).
          startedAt: last && !active ? null : e.occurredAt,
          finishedAt: last ? null : log[i + 1].occurredAt,
        };
      }),
    [log]
  );

  const [confirmAbort, setConfirmAbort] = React.useState(false);
  const [confirmRollback, setConfirmRollback] = React.useState(false);
  const [confirmDelete, setConfirmDelete] = React.useState(false);

  if (loading && !d) {
    return (
      <PageShell title={t("loadingTitle")} description={t("loading")}>
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-48 w-full" />
      </PageShell>
    );
  }

  if (!d) {
    return (
      <PageShell title={t("notFoundTitle")} description={t("notFoundDescription")}>
        <Card>
          <CardContent className="text-muted-foreground p-6 text-sm">
            {t("returnLink")}{" "}
            <Link href="/deployments" className="underline">
              {t("deploymentsList")}
            </Link>
            .
          </CardContent>
        </Card>
      </PageShell>
    );
  }

  const inFlight = IN_FLIGHT.has(d.status);
  const showApprove =
    d.status === "pending_approval" && d.approvalsReceived < d.approvalsRequired && canApprove;
  const showAbort = inFlight && canDeploy;
  const showRollback = d.status === "running" && canRollback;
  const showRedeploy = (d.status === "running" || d.status === "failed") && canDeploy;
  // Dismiss / delete: terminal rows (failed / superseded / rolled_back)
  // soft-delete; a running row is superseded. Same app.deploy gate as
  // abort. Hidden for in-flight rows — those abort first.
  const showDelete =
    (d.status === "failed" ||
      d.status === "superseded" ||
      d.status === "rolled_back" ||
      d.status === "running") &&
    canDeploy;

  // The dense middle of the page is split into two panes at md+ (#1056b):
  // left = DAG + approval/release context, right = the logs + events
  // stream. Below md the same cards stack. Each card is built once and
  // referenced from whichever layout is live.
  const dagCard = (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Deployment flow</CardTitle>
      </CardHeader>
      <CardContent>
        {logLoading && log.length === 0 ? (
          <Skeleton className="h-40 w-full" />
        ) : dagStages.length === 0 ? (
          <p className="text-muted-foreground text-sm">{t("noLog")}</p>
        ) : (
          <PipelineDag
            key={`${log.length}:${log[log.length - 1]?.id ?? ""}`}
            stages={dagStages}
            height={240}
            variant="telemetry"
          />
        )}
      </CardContent>
    </Card>
  );

  const lifecycleCard = (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{t("lifecycle")}</CardTitle>
      </CardHeader>
      <CardContent>
        {logLoading && log.length === 0 ? (
          <Skeleton className="h-24 w-full" />
        ) : log.length === 0 ? (
          <p className="text-muted-foreground text-sm">{t("noLog")}</p>
        ) : (
          <ol className="border-muted relative ml-3 space-y-4 border-l pl-4">
            {log.map((e) => (
              <li key={e.id} className="relative">
                <span className="bg-background border-muted-foreground absolute top-1 -left-[21px] size-3 rounded-full border" />
                <div className="flex flex-wrap items-baseline gap-2">
                  <span className="font-mono text-sm capitalize">
                    {e.status.replace(/_/g, " ")}
                  </span>
                  <span className="text-muted-foreground text-xs">{formatTime(e.occurredAt)}</span>
                </div>
                {e.message && <div className="text-sm">{e.message}</div>}
                {e.detail && Object.keys(e.detail).length > 0 && (
                  <pre className="bg-muted mt-1 max-h-40 overflow-auto rounded p-2 text-xs">
                    {JSON.stringify(e.detail, null, 2)}
                  </pre>
                )}
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  );

  // #657 / #738 — Release notes block. When the backend resolver
  // returns content (PR descriptions + commit subjects between the
  // previous successful deploy and this one), we render the full
  // structured block; otherwise fall back to the raw commit message
  // expander so the card is always non-empty when there's text.
  const releaseNotesCard =
    releaseNotes || d.commitMessage ? (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Release notes</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          {releaseNotes ? (
            <>
              {releaseNotes.pullRequests.length > 0 && (
                <ul className="space-y-2">
                  {releaseNotes.pullRequests.map((pr) => (
                    <li key={pr.number} className="border-muted border-l-2 pl-3">
                      <span className="font-medium">{pr.title}</span>
                      {pr.prUrl && (
                        <a
                          href={pr.prUrl}
                          target="_blank"
                          rel="noreferrer"
                          className="text-muted-foreground ml-2 text-xs hover:underline"
                        >
                          #{pr.number}
                        </a>
                      )}
                      {pr.body && (
                        <p className="text-muted-foreground mt-1 text-xs whitespace-pre-wrap">
                          {pr.body.slice(0, 400)}
                          {pr.body.length > 400 && "…"}
                        </p>
                      )}
                    </li>
                  ))}
                </ul>
              )}
              {releaseNotes.commits.filter((c) => !c.isMerge).length > 0 && (
                <details>
                  <summary className="text-muted-foreground cursor-pointer text-xs">
                    {releaseNotes.commits.filter((c) => !c.isMerge).length} commits
                  </summary>
                  <ul className="mt-2 space-y-1">
                    {releaseNotes.commits
                      .filter((c) => !c.isMerge)
                      .map((c) => (
                        <li key={c.sha} className="text-muted-foreground font-mono text-xs">
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
                  View full diff →
                </a>
              )}
            </>
          ) : (
            d.commitMessage && (
              <details>
                <summary className="text-muted-foreground cursor-pointer text-xs">
                  {d.commitMessage.split("\n")[0].slice(0, 120)}
                  {d.commitMessage.length > 120 && "…"}
                </summary>
                <pre className="bg-muted mt-2 max-h-64 overflow-auto rounded p-3 font-mono text-xs whitespace-pre-wrap">
                  {d.commitMessage}
                </pre>
              </details>
            )
          )}
        </CardContent>
      </Card>
    ) : null;

  // #653 — approval/review trail. Shows the full audit chain
  // (proposed → reviewed → approved → deployed) when the deploy
  // gated through quorum; collapses to a single triggered-by row
  // when no approval was required. Lazy-loaded so an open detail
  // page on an ungated deploy stays cheap.
  const activityCard = (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Activity</CardTitle>
      </CardHeader>
      <CardContent>
        {approvalHistoryLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : approvalHistory.length === 0 ? (
          <div className="text-muted-foreground text-sm">
            {d.triggeredByUserId
              ? `Triggered by user ${d.triggeredByUserId} via ${d.triggerKind}.`
              : `Triggered automatically via ${d.triggerKind}${d.ciProvider ? ` (${d.ciProvider})` : ""}.`}
            {d.approvalsRequired === 0 && " No approvals required for this environment."}
          </div>
        ) : (
          <ol className="border-muted relative ml-3 space-y-3 border-l pl-4 text-sm">
            {approvalHistory.map((e) => (
              <li key={e.id} className="relative">
                <span className="bg-background border-muted-foreground absolute top-1.5 -left-[21px] size-3 rounded-full border" />
                <div className="flex flex-wrap items-baseline gap-2">
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
                  <span className="text-muted-foreground font-mono text-xs">
                    {e.actorDisplay} ({e.actorKind})
                  </span>
                  <span className="text-muted-foreground ml-auto text-xs">
                    {formatTime(e.occurredAt)}
                  </span>
                </div>
                {e.reason && <div className="text-muted-foreground mt-0.5 italic">{e.reason}</div>}
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  );

  const eventsCard = (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{t("events")}</CardTitle>
      </CardHeader>
      <CardContent>
        {eventsLoading ? (
          <Skeleton className="h-24 w-full" />
        ) : (
          (() => {
            const filtered = events.filter(
              (e) => e.registeredAppId && d.registeredAppSlug && e.registeredAppId.length > 0
            );
            if (filtered.length === 0) {
              return <p className="text-muted-foreground text-sm">{t("noEvents")}</p>;
            }
            return (
              <ul className="divide-y">
                {filtered.slice(0, 12).map((e) => (
                  <li key={e.id} className="py-2 text-sm">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono text-xs">{e.eventType}</span>
                      <span className="text-muted-foreground text-xs">
                        {formatTime(e.occurredAt)}
                      </span>
                    </div>
                  </li>
                ))}
              </ul>
            );
          })()
        )}
      </CardContent>
    </Card>
  );

  return (
    <PageShell
      title={t("title", { app: d.registeredAppSlug, env: d.environmentName })}
      description={t("subtitle", { id: d.id })}
      actions={
        <div className="flex items-center gap-2">
          {showApprove && (
            <Can permission="app.approve_deploy">
              <Button size="sm" disabled={busy} onClick={onApprove}>
                <CheckIcon className="size-4" /> {t("approve")}
              </Button>
            </Can>
          )}
          {showAbort && (
            <Can permission="app.deploy">
              <Button
                size="sm"
                variant="destructive"
                disabled={busy}
                onClick={() => setConfirmAbort(true)}
              >
                <StopCircleIcon className="size-4" /> {t("abort")}
              </Button>
            </Can>
          )}
          {showRollback && (
            <Can permission="app.rollback">
              <Button
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() => setConfirmRollback(true)}
              >
                <UndoIcon className="size-4" /> {t("rollback")}
              </Button>
            </Can>
          )}
          {showRedeploy && (
            <Can permission="app.deploy">
              <Button size="sm" variant="outline" disabled={busy} onClick={onRedeploy}>
                <RotateCcwIcon className="size-4" /> {t("redeploy")}
              </Button>
            </Can>
          )}
          {showDelete && (
            <Can permission="app.deploy">
              <Button
                size="sm"
                variant="destructive"
                disabled={busy}
                onClick={() => setConfirmDelete(true)}
              >
                <Trash2Icon className="size-4" />{" "}
                {d.status === "running" ? t("retire") : t("delete")}
              </Button>
            </Can>
          )}
        </div>
      }
    >
      {/* #1553: say so when this rollout rendered the stored manifest
       * instead of the repo's. Above the status card because it changes
       * what a green deploy means. */}
      <StaleManifestNotice
        status={d.manifestResyncStatus}
        error={d.manifestResyncError}
        appSlug={d.registeredAppSlug}
      />
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-3">
            <StatusDot status={statusToDot[d.status]} />
            <DeploymentStatusPill status={d.status} />
            {d.approvalsRequired > 0 && (
              <Badge variant="secondary">
                {t("approvalsCount", {
                  received: d.approvalsReceived,
                  required: d.approvalsRequired,
                })}
              </Badge>
            )}
            <Badge variant="outline" className="font-mono">
              {d.triggerKind}
            </Badge>
            {d.strategy && d.strategy !== "unknown" && (
              <Badge variant="outline" className="font-mono capitalize">
                {d.strategy.replace(/_/g, " ")}
              </Badge>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-x-8 gap-y-3 text-sm sm:grid-cols-3">
          <Field label={t("fields.imageTag")} mono value={d.imageTag || "—"} />
          <Field label={t("fields.imageDigest")} mono value={d.imageDigest || "—"} />
          <Field label={t("fields.clusterRevision")} mono value={d.clusterRevision || "—"} />
          <Field label={t("fields.workload")} mono value={d.workloadSlug || "—"} />
          <Field label={t("fields.created")} value={formatTime(d.createdAt)} />
          <Field label={t("fields.started")} value={formatTime(d.startedAt)} />
          <Field label={t("fields.succeeded")} value={formatTime(d.succeededAt)} />
          <Field label={t("fields.failed")} value={formatTime(d.failedAt)} />
          <Field
            label={t("fields.duration")}
            value={
              <span className="inline-flex items-center gap-1">
                <ClockIcon className="size-3" />
                {formatDuration(d.durationSeconds)}
              </span>
            }
          />
          {(d.commitSha || d.branch || d.ciRunUrl) && (
            <>
              {d.commitSha && (
                <Field label={t("fields.commit")} mono value={d.commitSha.slice(0, 12)} />
              )}
              {d.branch && <Field label={t("fields.branch")} mono value={d.branch} />}
              {d.ciActorKind && <Field label={t("fields.ciActor")} mono value={d.ciActorKind} />}
              {d.ciProvider && <Field label={t("fields.ciProvider")} mono value={d.ciProvider} />}
              {d.ciRunUrl && (
                <Field
                  label={t("fields.ciRun")}
                  value={
                    <a
                      href={d.ciRunUrl}
                      target="_blank"
                      rel="noreferrer"
                      className="font-mono text-sm hover:underline"
                    >
                      {d.ciRunUrl}
                    </a>
                  }
                />
              )}
            </>
          )}
        </CardContent>
      </Card>

      {isMobile ? (
        <>
          {dagCard}
          {lifecycleCard}
          {releaseNotesCard}
          {activityCard}
          {eventsCard}
        </>
      ) : (
        <PanelGroup
          orientation="horizontal"
          id="deployment-detail-split"
          defaultLayout={splitLayout.defaultLayout}
          onLayoutChanged={splitLayout.onLayoutChanged}
        >
          <Panel id="deployment-context" defaultSize="55%" minSize="30%">
            <div className="flex h-full flex-col gap-6 overflow-y-auto">
              {dagCard}
              {releaseNotesCard}
              {activityCard}
            </div>
          </Panel>
          <PanelResizeHandle className="bg-border/50 hover:bg-border focus-visible:ring-ring mx-2 flex w-2 items-center justify-center rounded-full transition-colors focus-visible:ring-2 focus-visible:outline-none">
            <GripVerticalIcon className="text-muted-foreground size-4" />
          </PanelResizeHandle>
          <Panel id="deployment-streams" defaultSize="45%" minSize="25%">
            <div className="flex h-full flex-col gap-6 overflow-y-auto">
              {lifecycleCard}
              {eventsCard}
            </div>
          </Panel>
        </PanelGroup>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("manifests")}</CardTitle>
        </CardHeader>
        <CardContent>
          {manifestLoading ? (
            <Skeleton className="h-32 w-full" />
          ) : manifest?.error ? (
            <div className="text-destructive text-sm">
              <p className="font-medium">{t("renderFailed")}</p>
              <p className="mt-1">{manifest.error}</p>
              {manifest.errorPath && (
                <p className="text-muted-foreground mt-1 font-mono text-xs">
                  {manifest.errorPath}
                  {manifest.errorLine != null && ` :${manifest.errorLine}`}
                </p>
              )}
            </div>
          ) : manifest ? (
            <pre className="bg-muted max-h-[480px] overflow-auto rounded p-3 font-mono text-xs leading-relaxed">
              {JSON.stringify(manifest.resources, null, 2)}
            </pre>
          ) : (
            <p className="text-muted-foreground text-sm">{t("noManifests")}</p>
          )}
        </CardContent>
      </Card>

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
        title={t("confirmRollback.title", {
          app: d.registeredAppSlug,
          env: d.environmentName,
        })}
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
    </PageShell>
  );
}

function Field({ label, value, mono }: { label: string; value: React.ReactNode; mono?: boolean }) {
  return (
    <div>
      <div className="text-muted-foreground text-xs tracking-wide uppercase">{label}</div>
      <div className={mono ? "font-mono text-sm" : "text-sm"}>{value}</div>
    </div>
  );
}
