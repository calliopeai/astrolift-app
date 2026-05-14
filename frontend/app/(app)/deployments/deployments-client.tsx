"use client";

import { useMutation, useQuery, useSubscription } from "@apollo/client/react";
import {
  BoxIcon,
  CheckIcon,
  ClockIcon,
  MoreHorizontalIcon,
  PlusIcon,
  RotateCcwIcon,
  StopCircleIcon,
  UndoIcon,
} from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import {
  ABORT_DEPLOYMENT,
  APPROVE_DEPLOYMENT,
  REDEPLOY_APP,
  ROLLBACK_DEPLOYMENT,
} from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_DEPLOYMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import { DEPLOYMENT_LIFECYCLE_STREAM } from "@/graphql/lifecycle/lifecycle.subscriptions";
import type {
  AstroliftDeployment,
  DeploymentStatus,
} from "@/graphql/lifecycle/lifecycle.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import { StartDeploymentDialog } from "./start-deployment-dialog";

interface MutationResultLite<T> {
  ok: boolean;
  errors: { code: string; message: string }[];
  data: T | null;
}

interface Resp {
  astroliftDeployments: AstroliftDeployment[];
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

const IN_FLIGHT: DeploymentStatus[] = [
  "pending_approval",
  "pending",
  "deploying",
  "redeploying",
];

const STATUS_OPTIONS: { value: DeploymentStatus | "all"; label: string }[] = [
  { value: "all", label: "All statuses" },
  { value: "pending_approval", label: "Pending approval" },
  { value: "pending", label: "Pending" },
  { value: "deploying", label: "Deploying" },
  { value: "redeploying", label: "Redeploying" },
  { value: "running", label: "Running" },
  { value: "failed", label: "Failed" },
  { value: "rolled_back", label: "Rolled back" },
  { value: "superseded", label: "Superseded" },
];

export function DeploymentsClient() {
  const [openCreate, setOpenCreate] = React.useState(false);
  const [statusFilter, setStatusFilter] = React.useState<DeploymentStatus | "all">("all");
  const [appFilter, setAppFilter] = React.useState("");
  const { can } = useMyPermissions();
  const { data, loading, refetch: refetchList } = useQuery<Resp>(
    LIST_DEPLOYMENTS,
    {
      variables: { limit: 100 },
      // Live push covers freshness; keep a slow safety-net poll
      // in case the WS drops and we miss reconnect.
      pollInterval: 30000,
    },
  );
  const allDeployments = data?.astroliftDeployments ?? [];
  const list = React.useMemo(() => {
    return allDeployments.filter((d) => {
      if (statusFilter !== "all" && d.status !== statusFilter) return false;
      if (appFilter && !d.registeredAppSlug.toLowerCase().includes(appFilter.toLowerCase())) {
        return false;
      }
      return true;
    });
  }, [allDeployments, statusFilter, appFilter]);

  // Live push: any status transition for any deployment in the org
  // triggers a list refetch. The backend dedupes per-row, and
  // refetch is cheap because the page is bounded to 100 rows.
  useSubscription(DEPLOYMENT_LIFECYCLE_STREAM, {
    onData: () => {
      refetchList().catch(() => {
        // swallowed: a failed refetch is recovered by the next push
        // or by the safety-net poll above.
      });
    },
  });

  // Pre-compute action allowance once to avoid re-checks in render.
  const canDeploy = can("app.deploy");
  const canApprove = can("app.approve_deploy");
  const canRollback = can("app.rollback");
  const hasAnyAction = canDeploy || canApprove || canRollback;

  const refetch = [{ query: LIST_DEPLOYMENTS, variables: { limit: 100 } }];
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

  type ActionKind = "approve" | "abort" | "rollback" | "redeploy";
  const [pendingAction, setPendingAction] = React.useState<
    { kind: ActionKind; deployment: AstroliftDeployment } | null
  >(null);

  async function runAction(kind: ActionKind, d: AstroliftDeployment) {
    if (kind === "approve") {
      const { data } = await approve({ variables: { input: { id: d.id } } });
      reportResult("approveDeployment", data?.approveDeployment);
    } else if (kind === "abort") {
      const { data } = await abort({ variables: { input: { id: d.id } } });
      reportResult("abortDeployment", data?.abortDeployment);
    } else if (kind === "rollback") {
      const { data } = await rollback({ variables: { input: { id: d.id } } });
      reportResult("rollbackDeployment", data?.rollbackDeployment);
    } else if (kind === "redeploy") {
      const { data } = await redeploy({ variables: { input: { id: d.id } } });
      reportResult("redeployApp", data?.redeployApp);
    }
  }

  const ACTION_COPY: Record<
    ActionKind,
    {
      title: (d: AstroliftDeployment) => string;
      description: (d: AstroliftDeployment) => string;
      confirmLabel: string;
      destructive: boolean;
    }
  > = {
    approve: {
      title: (d) => `Approve deploy of ${d.imageTag}?`,
      description: (d) =>
        `Unblocks the rollout to ${d.registeredAppSlug}/${d.environmentName}. The workflow resumes immediately.`,
      confirmLabel: "Approve",
      destructive: false,
    },
    abort: {
      title: (d) =>
        `Abort in-flight deploy of ${d.registeredAppSlug}/${d.environmentName}?`,
      description: () =>
        "Signals the Temporal workflow to cancel. The deployment is marked failed. Whatever changes already shipped to the cluster stay — Astrolift doesn't auto-rollback on abort.",
      confirmLabel: "Abort deploy",
      destructive: true,
    },
    rollback: {
      title: (d) =>
        `Rollback ${d.registeredAppSlug}/${d.environmentName} to the previous revision?`,
      description: () =>
        "Creates a new rollback deployment that points at the prior running revision. The current revision becomes superseded.",
      confirmLabel: "Rollback",
      destructive: false,
    },
    redeploy: {
      title: (d) =>
        `Redeploy ${d.imageTag} to ${d.registeredAppSlug}/${d.environmentName}?`,
      description: () =>
        "Spawns a fresh deployment with the same image. Useful to retry after a transient failure or pick up an updated config.",
      confirmLabel: "Redeploy",
      destructive: false,
    },
  };

  return (
    <PageShell
      title="Deployments"
      description="Every rollout attempt across every app and environment. Click a row to see the workflow timeline, rendered manifests, and logs."
      actions={
        <Can permission="app.deploy">
          <Button onClick={() => setOpenCreate(true)}>
            <PlusIcon className="size-4" />
            Start deployment
          </Button>
        </Can>
      }
    >
      <div className="flex flex-wrap items-center gap-2">
        <Input
          placeholder="Filter by app slug…"
          value={appFilter}
          onChange={(e) => setAppFilter(e.target.value)}
          className="max-w-xs"
        />
        <Select
          value={statusFilter}
          onValueChange={(v) => setStatusFilter(v as DeploymentStatus | "all")}
        >
          <SelectTrigger className="w-48">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {STATUS_OPTIONS.map((opt) => (
              <SelectItem key={opt.value} value={opt.value}>
                {opt.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span className="text-muted-foreground text-xs">
          {list.length} of {allDeployments.length}
        </span>
      </div>

      <Card>
        <CardContent className="p-0">
          {loading && list.length === 0 ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : list.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<BoxIcon className="size-5" />}
                title="No deployments yet"
                description="Register an app and roll a deployment from /apps/[slug]/deploy. Deployments land here as soon as the workflow starts."
                actionHref="/apps"
                actionLabel="Open apps"
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead></TableHead>
                  <TableHead>App / Env</TableHead>
                  <TableHead>Image</TableHead>
                  <TableHead>Trigger</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Duration</TableHead>
                  <TableHead>Started</TableHead>
                  <TableHead className="w-12"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.map((d) => (
                  <TableRow key={d.id}>
                    <TableCell className="w-8">
                      <StatusDot status={statusToDot[d.status]} />
                    </TableCell>
                    <TableCell>
                      <a
                        href={`/deployments/${d.id}`}
                        className="hover:underline"
                      >
                        <div className="font-medium">{d.registeredAppSlug}</div>
                        <div className="text-muted-foreground text-xs">
                          env <span className="font-mono">{d.environmentName}</span>
                          {d.workloadSlug && (
                            <>
                              {" "}
                              · workload{" "}
                              <span className="font-mono">{d.workloadSlug}</span>
                            </>
                          )}
                        </div>
                      </a>
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {d.imageTag || "—"}
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline">{d.triggerKind}</Badge>
                    </TableCell>
                    <TableCell>
                      <Badge variant="secondary" className="capitalize">
                        {d.status.replace(/_/g, " ")}
                      </Badge>
                      {d.approvalsRequired > 0 && (
                        <div className="text-muted-foreground mt-1 text-xs">
                          {d.approvalsReceived}/{d.approvalsRequired} approvals
                        </div>
                      )}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      <span className="inline-flex items-center gap-1">
                        <ClockIcon className="size-3" />
                        {formatDuration(d.durationSeconds)}
                      </span>
                    </TableCell>
                    <TableCell className="text-muted-foreground text-sm">
                      {d.startedAt
                        ? new Date(d.startedAt).toLocaleString()
                        : new Date(d.createdAt).toLocaleString()}
                    </TableCell>
                    <TableCell className="text-right">
                      {hasAnyAction ? (
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button
                              variant="ghost"
                              size="icon"
                              className="size-8"
                              disabled={busy}
                            >
                              <MoreHorizontalIcon className="size-4" />
                              <span className="sr-only">Actions</span>
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            {d.status === "pending_approval" && canApprove && (
                              <DropdownMenuItem
                                onClick={() => setPendingAction({ kind: "approve", deployment: d })}
                              >
                                <CheckIcon className="size-4" />
                                Approve
                              </DropdownMenuItem>
                            )}
                            {IN_FLIGHT.includes(d.status) && canDeploy && (
                              <DropdownMenuItem
                                onClick={() => setPendingAction({ kind: "abort", deployment: d })}
                                variant="destructive"
                              >
                                <StopCircleIcon className="size-4" />
                                Abort
                              </DropdownMenuItem>
                            )}
                            {d.status === "running" && canRollback && (
                              <DropdownMenuItem
                                onClick={() => setPendingAction({ kind: "rollback", deployment: d })}
                              >
                                <UndoIcon className="size-4" />
                                Rollback
                              </DropdownMenuItem>
                            )}
                            {(d.status === "running" ||
                              d.status === "failed" ||
                              d.status === "rolled_back" ||
                              d.status === "superseded") &&
                              canDeploy && (
                                <DropdownMenuItem
                                  onClick={() =>
                                    setPendingAction({ kind: "redeploy", deployment: d })
                                  }
                                >
                                  <RotateCcwIcon className="size-4" />
                                  Redeploy
                                </DropdownMenuItem>
                              )}
                          </DropdownMenuContent>
                        </DropdownMenu>
                      ) : null}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <StartDeploymentDialog open={openCreate} onOpenChange={setOpenCreate} />

      <ConfirmDialog
        open={pendingAction !== null}
        onOpenChange={(next) => {
          if (!next) setPendingAction(null);
        }}
        title={
          pendingAction ? ACTION_COPY[pendingAction.kind].title(pendingAction.deployment) : ""
        }
        description={
          pendingAction
            ? ACTION_COPY[pendingAction.kind].description(pendingAction.deployment)
            : ""
        }
        confirmLabel={pendingAction ? ACTION_COPY[pendingAction.kind].confirmLabel : "Confirm"}
        destructive={pendingAction ? ACTION_COPY[pendingAction.kind].destructive : false}
        onConfirm={async () => {
          if (pendingAction) await runAction(pendingAction.kind, pendingAction.deployment);
        }}
      />
    </PageShell>
  );
}
