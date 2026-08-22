"use client";

import { useMutation, useQuery, useSubscription } from "@apollo/client/react";
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
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import * as React from "react";
import {
  Group as PanelGroup,
  Panel,
  Separator as PanelResizeHandle,
  useDefaultLayout,
} from "react-resizable-panels";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ConfirmDialogWithReason } from "@/components/ConfirmDialogWithReason";
import { DeploymentStatusPill } from "@/components/DeploymentStatusPill";
import { PageShell } from "@/components/PageShell";
import { StaleManifestNotice } from "@/components/StaleManifestNotice";
import { StatusDot } from "@/components/StatusDot";
import { Can } from "@/components/Can";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { PipelineDag, type PipelineDagStage } from "@/components/viz";
import {
  ABORT_DEPLOYMENT,
  APPROVE_DEPLOYMENT,
  DELETE_DEPLOYMENT,
  REDEPLOY_APP,
  ROLLBACK_DEPLOYMENT,
} from "@/graphql/lifecycle/lifecycle.mutations";
import {
  GET_DEPLOYMENT,
  GET_DEPLOYMENT_APPROVAL_HISTORY,
  GET_DEPLOYMENT_LOG,
  GET_DEPLOYMENT_RELEASE_NOTES,
} from "@/graphql/lifecycle/lifecycle.queries";
import { LIST_EVENTS } from "@/graphql/operations/operations.queries";
import { GET_RENDERED_MANIFEST } from "@/graphql/registry/registry.queries";
import { DEPLOYMENT_LIFECYCLE_STREAM } from "@/graphql/lifecycle/lifecycle.subscriptions";
import type {
  AstroliftDeployment,
  AstroliftDeploymentLogEntry,
  AstroliftReleaseNotes,
  DeploymentStatus,
} from "@/graphql/lifecycle/lifecycle.types";
import { useIsMobile } from "@/hooks/use-mobile";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

interface MutationResultLite<T> {
  ok: boolean;
  errors: { code: string; message: string }[];
  data: T | null;
}

interface DeploymentResp {
  astroliftDeployment: AstroliftDeployment | null;
}

interface LogResp {
  astroliftDeploymentLog: AstroliftDeploymentLogEntry[];
}

interface ManifestResp {
  astroliftRenderedManifest: {
    appSlug: string;
    environmentName?: string | null;
    imageTag?: string | null;
    namespace: string;
    resources: Record<string, unknown>;
    error?: string | null;
    errorPath?: string | null;
    errorLine?: number | null;
    errorColumn?: number | null;
  } | null;
}

interface EventsResp {
  astroliftEvents: Array<{
    id: string;
    eventType: string;
    payload: Record<string, unknown>;
    registeredAppId?: string | null;
    occurredAt: string;
  }>;
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

function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}m ${s}s`;
}

function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}

const IN_FLIGHT: DeploymentStatus[] = ["pending_approval", "pending", "deploying", "redeploying"];

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

export function DeploymentDetailClient({ id }: { id: string }) {
  const t = useTranslations("lists.deploymentDetail");
  const { can } = useMyPermissions();
  const router = useRouter();

  const {
    data: dData,
    loading: dLoading,
    refetch: refetchDeployment,
  } = useQuery<DeploymentResp>(GET_DEPLOYMENT, { variables: { id } });
  const {
    data: lData,
    loading: lLoading,
    refetch: refetchLog,
  } = useQuery<LogResp>(GET_DEPLOYMENT_LOG, {
    variables: { deploymentId: id },
  });

  const deployment = dData?.astroliftDeployment ?? null;
  const manifest = useQuery<ManifestResp>(GET_RENDERED_MANIFEST, {
    variables: {
      appSlug: deployment?.registeredAppSlug ?? "",
      environmentName: deployment?.environmentName ?? null,
      imageTag: deployment?.imageTag ?? null,
    },
    skip: !deployment,
    fetchPolicy: "cache-first",
  });

  const events = useQuery<EventsResp>(LIST_EVENTS, {
    variables: { limit: 50 },
    skip: !deployment,
    fetchPolicy: "cache-and-network",
  });

  // #653 approval / review trail. Fetched lazily — only deploys that
  // gated through quorum produce history rows, so the resolver returns
  // [] for the trivial "no approvals required" case.
  const approvalHistory = useQuery<{
    astroliftDeploymentApprovalHistory: Array<{
      id: string;
      action: string;
      decision: string;
      actorKind: string;
      actorId: string;
      actorDisplay: string;
      occurredAt: string;
      reason: string;
    }>;
  }>(GET_DEPLOYMENT_APPROVAL_HISTORY, {
    variables: { deploymentId: id },
    skip: !deployment,
    fetchPolicy: "cache-and-network",
  });

  // #738 — release notes: diff between prev-successful deploy SHA and
  // this deploy's SHA. Fetched lazily after the deployment row lands.
  const releaseNotesQuery = useQuery<{
    astroliftDeploymentReleaseNotes: AstroliftReleaseNotes | null;
  }>(GET_DEPLOYMENT_RELEASE_NOTES, {
    variables: { deploymentId: id },
    skip: !deployment,
    fetchPolicy: "cache-and-network",
  });
  const releaseNotes = releaseNotesQuery.data?.astroliftDeploymentReleaseNotes ?? null;

  // Live push: any lifecycle event triggers both refetches when the
  // event is for this deployment. Unrelated org events don't churn
  // the page. The subscription payload is loosely typed in Apollo's
  // generic; we narrow at the use-site.
  useSubscription(DEPLOYMENT_LIFECYCLE_STREAM, {
    onData: ({ data: payload }) => {
      const stream = payload.data as
        | { astroliftDeploymentLifecycleStream?: { deploymentId?: string } }
        | undefined;
      const evt = stream?.astroliftDeploymentLifecycleStream;
      if (evt && evt.deploymentId === id) {
        refetchDeployment().catch(() => {});
        refetchLog().catch(() => {});
      }
    },
  });

  const refetch = [
    { query: GET_DEPLOYMENT, variables: { id } },
    { query: GET_DEPLOYMENT_LOG, variables: { deploymentId: id } },
  ];
  const [approve, approveState] = useMutation<{
    approveDeployment: MutationResultLite<AstroliftDeployment>;
  }>(APPROVE_DEPLOYMENT, { refetchQueries: refetch });
  const [abort, abortState] = useMutation<{
    abortDeployment: MutationResultLite<AstroliftDeployment>;
  }>(ABORT_DEPLOYMENT, { refetchQueries: refetch });
  const [rollback, rollbackState] = useMutation<{
    rollbackDeployment: MutationResultLite<AstroliftDeployment>;
  }>(ROLLBACK_DEPLOYMENT, { refetchQueries: refetch });
  const [redeploy, redeployState] = useMutation<{
    redeployApp: MutationResultLite<AstroliftDeployment>;
  }>(REDEPLOY_APP, { refetchQueries: refetch });
  const [deleteDeployment, deleteState] = useMutation<{
    deleteDeployment: MutationResultLite<Pick<AstroliftDeployment, "id" | "status">>;
  }>(DELETE_DEPLOYMENT);

  const busy =
    approveState.loading ||
    abortState.loading ||
    rollbackState.loading ||
    redeployState.loading ||
    deleteState.loading;

  const d = dData?.astroliftDeployment ?? null;
  const log = React.useMemo(() => lData?.astroliftDeploymentLog ?? [], [lData]);

  // Split-pane plumbing (#1056b): stacked below md, resizable side-by-side
  // at md+. All data hooks live at this top level, so switching layouts
  // never remounts the lifecycle subscription or any query.
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
        const active = IN_FLIGHT.includes(e.status as DeploymentStatus) || e.status === "running";
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

  function reportResult(
    label: string,
    result: MutationResultLite<AstroliftDeployment> | null | undefined
  ) {
    if (!result) return;
    if (result.ok) {
      toast.success(`${label}: ${result.data?.status ?? "ok"}`);
    } else {
      throw new Error(result.errors[0]?.message ?? `${label} failed`);
    }
  }

  const [confirmAbort, setConfirmAbort] = React.useState(false);
  const [confirmRollback, setConfirmRollback] = React.useState(false);
  const [confirmDelete, setConfirmDelete] = React.useState(false);

  if (dLoading && !d) {
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

  const inFlight = IN_FLIGHT.includes(d.status);
  const showApprove =
    d.status === "pending_approval" &&
    d.approvalsReceived < d.approvalsRequired &&
    can("app.approve_deploy");
  const showAbort = inFlight && can("app.deploy");
  const showRollback = d.status === "running" && can("app.rollback");
  const showRedeploy = (d.status === "running" || d.status === "failed") && can("app.deploy");
  // Dismiss / delete: terminal rows (failed / superseded / rolled_back)
  // soft-delete; a running row is superseded. Same app.deploy gate as
  // abort. Hidden for in-flight rows — those abort first.
  const showDelete =
    (d.status === "failed" ||
      d.status === "superseded" ||
      d.status === "rolled_back" ||
      d.status === "running") &&
    can("app.deploy");

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
        {lLoading && log.length === 0 ? (
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
        {lLoading && log.length === 0 ? (
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
        <CardContent className="text-sm space-y-3">
          {releaseNotes ? (
            <>
              {releaseNotes.pullRequests.length > 0 && (
                <ul className="space-y-2">
                  {releaseNotes.pullRequests.map((pr) => (
                    <li key={pr.number} className="border-l-2 border-muted pl-3">
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
        {approvalHistory.loading && !approvalHistory.data ? (
          <Skeleton className="h-16 w-full" />
        ) : (approvalHistory.data?.astroliftDeploymentApprovalHistory ?? []).length === 0 ? (
          <div className="text-muted-foreground text-sm">
            {d.triggeredByUserId
              ? `Triggered by user ${d.triggeredByUserId} via ${d.triggerKind}.`
              : `Triggered automatically via ${d.triggerKind}${d.ciProvider ? ` (${d.ciProvider})` : ""}.`}
            {d.approvalsRequired === 0 && " No approvals required for this environment."}
          </div>
        ) : (
          <ol className="border-muted relative ml-3 space-y-3 border-l pl-4 text-sm">
            {(approvalHistory.data?.astroliftDeploymentApprovalHistory ?? []).map((e) => (
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
        {events.loading && !events.data ? (
          <Skeleton className="h-24 w-full" />
        ) : (
          (() => {
            const filtered = (events.data?.astroliftEvents ?? []).filter(
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
              <Button
                size="sm"
                disabled={busy}
                onClick={async () => {
                  const { data } = await approve({
                    variables: { input: { id: d.id } },
                  });
                  reportResult("approveDeployment", data?.approveDeployment);
                }}
              >
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
              <Button
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={async () => {
                  const { data } = await redeploy({
                    variables: {
                      input: {
                        appSlug: d.registeredAppSlug,
                        environmentName: d.environmentName,
                        imageTag: d.imageTag || undefined,
                      },
                    },
                  });
                  reportResult("redeployApp", data?.redeployApp);
                }}
              >
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
          {manifest.loading ? (
            <Skeleton className="h-32 w-full" />
          ) : manifest.data?.astroliftRenderedManifest?.error ? (
            <div className="text-destructive text-sm">
              <p className="font-medium">{t("renderFailed")}</p>
              <p className="mt-1">{manifest.data.astroliftRenderedManifest.error}</p>
              {manifest.data.astroliftRenderedManifest.errorPath && (
                <p className="text-muted-foreground mt-1 font-mono text-xs">
                  {manifest.data.astroliftRenderedManifest.errorPath}
                  {manifest.data.astroliftRenderedManifest.errorLine != null &&
                    ` :${manifest.data.astroliftRenderedManifest.errorLine}`}
                </p>
              )}
            </div>
          ) : manifest.data?.astroliftRenderedManifest ? (
            <pre className="bg-muted max-h-[480px] overflow-auto rounded p-3 font-mono text-xs leading-relaxed">
              {JSON.stringify(manifest.data.astroliftRenderedManifest.resources, null, 2)}
            </pre>
          ) : (
            <p className="text-muted-foreground text-sm">{t("noManifests")}</p>
          )}
        </CardContent>
      </Card>

      <ConfirmDialogWithReason
        open={confirmAbort}
        onOpenChange={setConfirmAbort}
        title={t("confirmAbort.title", { app: d.registeredAppSlug, env: d.environmentName })}
        description={t("confirmAbort.description")}
        reasonLabel={t("confirmAbort.reasonLabel")}
        reasonPlaceholder={t("confirmAbort.reasonPlaceholder")}
        reasonRequiredError={t("confirmAbort.reasonRequired")}
        confirmLabel={t("confirmAbort.confirm")}
        destructive
        onConfirm={async (reason) => {
          const { data } = await abort({
            variables: { input: { id: d.id, reason } },
          });
          reportResult("abortDeployment", data?.abortDeployment);
        }}
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
        onConfirm={async () => {
          const { data } = await rollback({ variables: { input: { id: d.id } } });
          reportResult("rollbackDeployment", data?.rollbackDeployment);
        }}
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
        onConfirm={async () => {
          const { data } = await deleteDeployment({ variables: { input: { id: d.id } } });
          const result = data?.deleteDeployment;
          if (result?.ok) {
            // Running rows are superseded (stay in history); terminal
            // rows are soft-deleted. Either way the operator is done
            // here, so bounce back to the list.
            toast.success(t("confirmDelete.success"));
            router.push("/deployments");
          } else if (result) {
            throw new Error(result.errors[0]?.message ?? t("confirmDelete.failed"));
          }
        }}
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
