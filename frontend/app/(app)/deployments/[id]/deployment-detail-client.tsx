"use client";

import { useMutation, useQuery, useSubscription } from "@apollo/client/react";
import {
  CheckIcon,
  ClockIcon,
  RotateCcwIcon,
  StopCircleIcon,
  UndoIcon,
} from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

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
      toast.error(result.errors[0]?.message ?? `${label} failed`);
    }
  }

  if (dLoading && !d) {
    return (
      <PageShell title="Deployment" description="Loading…">
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-48 w-full" />
      </PageShell>
    );
  }

  if (!d) {
    return (
      <PageShell
        title="Deployment not found"
        description="The deployment doesn't exist or isn't visible to your tenant."
      >
        <Card>
          <CardContent className="p-6 text-sm text-muted-foreground">
            Try returning to the{" "}
            <a href="/deployments" className="underline">
              deployments list
            </a>
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
      title={`${d.registeredAppSlug} → ${d.environmentName}`}
      description={`Deployment ${d.id}`}
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
                <CheckIcon className="size-4" /> Approve
              </Button>
            </Can>
          )}
          {showAbort && (
            <Can permission="app.deploy">
              <Button
                size="sm"
                variant="destructive"
                disabled={busy}
                onClick={async () => {
                  if (
                    !confirm(
                      `Abort in-flight deploy of ${d.registeredAppSlug}/${d.environmentName}?`,
                    )
                  )
                    return;
                  const { data } = await abort({
                    variables: { input: { id: d.id } },
                  });
                  reportResult("abortDeployment", data?.abortDeployment);
                }}
              >
                <StopCircleIcon className="size-4" /> Abort
              </Button>
            </Can>
          )}
          {showRollback && (
            <Can permission="app.rollback">
              <Button
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={async () => {
                  if (
                    !confirm(
                      `Rollback ${d.registeredAppSlug}/${d.environmentName}?`,
                    )
                  )
                    return;
                  const { data } = await rollback({
                    variables: { input: { id: d.id } },
                  });
                  reportResult("rollbackDeployment", data?.rollbackDeployment);
                }}
              >
                <UndoIcon className="size-4" /> Rollback
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
                <RotateCcwIcon className="size-4" /> Redeploy
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
                {d.approvalsReceived}/{d.approvalsRequired} approvals
              </Badge>
            )}
            <Badge variant="outline" className="font-mono">
              {d.triggerKind}
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-x-8 gap-y-3 text-sm sm:grid-cols-3">
          <Field label="Image tag" mono value={d.imageTag || "—"} />
          <Field label="Image digest" mono value={d.imageDigest || "—"} />
          <Field label="Cluster revision" mono value={d.clusterRevision || "—"} />
          <Field label="Workload" mono value={d.workloadSlug || "—"} />
          <Field label="Created" value={formatTime(d.createdAt)} />
          <Field label="Started" value={formatTime(d.startedAt)} />
          <Field label="Succeeded" value={formatTime(d.succeededAt)} />
          <Field label="Failed" value={formatTime(d.failedAt)} />
          <Field
            label="Duration"
            value={
              <span className="inline-flex items-center gap-1">
                <ClockIcon className="size-3" />
                {formatDuration(d.durationSeconds)}
              </span>
            }
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Lifecycle log</CardTitle>
        </CardHeader>
        <CardContent>
          {lLoading && log.length === 0 ? (
            <Skeleton className="h-24 w-full" />
          ) : log.length === 0 ? (
            <p className="text-muted-foreground text-sm">No log entries yet.</p>
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
