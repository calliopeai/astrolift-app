"use client";

import { useMutation, useQuery, useSubscription } from "@apollo/client/react";
import {
  CheckIcon,
  ClockIcon,
  RotateCcwIcon,
  StopCircleIcon,
  UndoIcon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { Can } from "@/components/Can";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ABORT_DEPLOYMENT,
  APPROVE_DEPLOYMENT,
  REDEPLOY_APP,
  ROLLBACK_DEPLOYMENT,
} from "@/graphql/lifecycle/lifecycle.mutations";
import {
  GET_DEPLOYMENT,
  GET_DEPLOYMENT_LOG,
} from "@/graphql/lifecycle/lifecycle.queries";
import { LIST_EVENTS } from "@/graphql/operations/operations.queries";
import { GET_RENDERED_MANIFEST } from "@/graphql/registry/registry.queries";
import { DEPLOYMENT_LIFECYCLE_STREAM } from "@/graphql/lifecycle/lifecycle.subscriptions";
import type {
  AstroliftDeployment,
  AstroliftDeploymentLogEntry,
  DeploymentStatus,
} from "@/graphql/lifecycle/lifecycle.types";
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

const IN_FLIGHT: DeploymentStatus[] = [
  "pending_approval",
  "pending",
  "deploying",
  "redeploying",
];

export function DeploymentDetailClient({ id }: { id: string }) {
  const t = useTranslations("lists.deploymentDetail");
  const { can } = useMyPermissions();

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

  // Live push: any lifecycle event triggers both refetches when the
  // event is for this deployment. Unrelated org events don't churn
  // the page. The subscription payload is loosely typed in Apollo's
  // generic; we narrow at the use-site.
  useSubscription(DEPLOYMENT_LIFECYCLE_STREAM, {
    onData: ({ data: payload }) => {
      const stream = (payload.data as
        | { astroliftDeploymentLifecycleStream?: { deploymentId?: string } }
        | undefined);
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

  const busy =
    approveState.loading ||
    abortState.loading ||
    rollbackState.loading ||
    redeployState.loading;

  const d = dData?.astroliftDeployment ?? null;
  const log = lData?.astroliftDeploymentLog ?? [];

  function reportResult(
    label: string,
    result: MutationResultLite<AstroliftDeployment> | null | undefined,
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
      <PageShell
        title={t("notFoundTitle")}
        description={t("notFoundDescription")}
      >
        <Card>
          <CardContent className="p-6 text-sm text-muted-foreground">
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
  const showRedeploy =
    (d.status === "running" || d.status === "failed") && can("app.deploy");

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
        </div>
      }
    >
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-3">
            <StatusDot status={statusToDot[d.status]} />
            <span className="capitalize">{d.status.replace(/_/g, " ")}</span>
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
              {d.ciActorKind && (
                <Field label={t("fields.ciActor")} mono value={d.ciActorKind} />
              )}
              {d.ciProvider && (
                <Field label={t("fields.ciProvider")} mono value={d.ciProvider} />
              )}
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
                  <span className="bg-background border-muted-foreground absolute -left-[21px] top-1 size-3 rounded-full border" />
                  <div className="flex flex-wrap items-baseline gap-2">
                    <span className="font-mono text-sm capitalize">
                      {e.status.replace(/_/g, " ")}
                    </span>
                    <span className="text-muted-foreground text-xs">
                      {formatTime(e.occurredAt)}
                    </span>
                  </div>
                  {e.message && (
                    <div className="text-sm">{e.message}</div>
                  )}
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
                (e) => e.registeredAppId && d.registeredAppSlug && e.registeredAppId.length > 0,
              );
              if (filtered.length === 0) {
                return (
                  <p className="text-muted-foreground text-sm">
                    {t("noEvents")}
                  </p>
                );
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

      <ConfirmDialog
        open={confirmAbort}
        onOpenChange={setConfirmAbort}
        title={t("confirmAbort.title", { app: d.registeredAppSlug, env: d.environmentName })}
        description={t("confirmAbort.description")}
        confirmLabel={t("confirmAbort.confirm")}
        destructive
        onConfirm={async () => {
          const { data } = await abort({ variables: { input: { id: d.id } } });
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
    </PageShell>
  );
}

function Field({
  label,
  value,
  mono,
}: {
  label: string;
  value: React.ReactNode;
  mono?: boolean;
}) {
  return (
    <div>
      <div className="text-muted-foreground text-xs uppercase tracking-wide">
        {label}
      </div>
      <div className={mono ? "font-mono text-sm" : "text-sm"}>{value}</div>
    </div>
  );
}
