"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { CheckCircle2Icon, ClockIcon, XCircleIcon } from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { APPROVE_DEPLOYMENT } from "@/graphql/lifecycle/lifecycle.mutations";
import { GET_DEPLOYMENT } from "@/graphql/lifecycle/lifecycle.queries";
import type {
  AstroliftDeployment,
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

function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}

export function ApprovalClient({ id }: { id: string }) {
  const { can } = useMyPermissions();
  const { data, loading, refetch } = useQuery<DeploymentResp>(GET_DEPLOYMENT, {
    variables: { id },
  });
  const d = data?.astroliftDeployment ?? null;

  const [approve, approveState] = useMutation<{
    approveDeployment: MutationResultLite<AstroliftDeployment>;
  }>(APPROVE_DEPLOYMENT, {
    refetchQueries: [{ query: GET_DEPLOYMENT, variables: { id } }],
  });

  if (loading && !d) {
    return (
      <PageShell title="Approve deployment" description="Loading…">
        <Skeleton className="h-32 w-full" />
      </PageShell>
    );
  }

  if (!d) {
    return (
      <PageShell
        title="Deployment not found"
        description="The deployment doesn't exist or you don't have permission to view it."
      >
        <Card>
          <CardContent className="p-6 text-sm text-muted-foreground">
            The approval link may have expired, or the deployment was deleted.
            Return to the{" "}
            <Link href="/deployments" className="underline">
              deployments list
            </Link>
            .
          </CardContent>
        </Card>
      </PageShell>
    );
  }

  const needsApproval =
    d.status === "pending_approval" &&
    d.approvalsReceived < d.approvalsRequired;
  const alreadyDecided =
    d.status === "deploying" ||
    d.status === "running" ||
    d.status === "rolled_back" ||
    d.status === "superseded";
  const failed = d.status === "failed";

  const [confirmApprove, setConfirmApprove] = React.useState(false);

  async function handleApprove() {
    const { data: result } = await approve({
      variables: { input: { id: d!.id } },
    });
    const r = result?.approveDeployment;
    if (r?.ok) {
      toast.success(`Approved — status now ${r.data?.status ?? "ok"}`);
      refetch().catch(() => {});
    } else {
      throw new Error(r?.errors[0]?.message ?? "approveDeployment failed");
    }
  }

  return (
    <PageShell
      title="Approve deployment"
      description={`${d.registeredAppSlug} → ${d.environmentName}`}
    >
      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center gap-3">
            <StatusDot status={statusToDot[d.status]} />
            <span className="capitalize">{d.status.replace(/_/g, " ")}</span>
            {d.approvalsRequired > 0 && (
              <Badge variant="secondary">
                {d.approvalsReceived} / {d.approvalsRequired} approvals
              </Badge>
            )}
            <Badge variant="outline" className="font-mono">
              {d.triggerKind}
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <dl className="grid grid-cols-2 gap-x-8 gap-y-2 text-sm">
            <Field label="Image tag" mono value={d.imageTag || "—"} />
            <Field
              label="Image digest"
              mono
              value={d.imageDigest || "—"}
            />
            <Field
              label="Cluster revision"
              mono
              value={d.clusterRevision || "—"}
            />
            <Field
              label="Workload"
              mono
              value={d.workloadSlug || "—"}
            />
            <Field label="Created" value={formatTime(d.createdAt)} />
            <Field label="Started" value={formatTime(d.startedAt)} />
          </dl>

          {needsApproval ? (
            <div className="bg-amber-100 border border-amber-300 rounded-md p-4 text-sm dark:bg-amber-950/40 dark:border-amber-900/60">
              <div className="flex items-center gap-2 font-medium">
                <ClockIcon className="size-4" /> Awaiting approval
              </div>
              <p className="text-muted-foreground mt-1">
                {d.approvalsRequired - d.approvalsReceived} more approval(s)
                required before this deployment proceeds. Self-approval is
                rejected by the backend.
              </p>
              <div className="mt-3 flex gap-2">
                {can("app.approve_deploy") ? (
                  <Can permission="app.approve_deploy">
                    <Button
                      onClick={() => setConfirmApprove(true)}
                      disabled={approveState.loading}
                    >
                      <CheckCircle2Icon className="size-4" /> Approve
                    </Button>
                  </Can>
                ) : (
                  <p className="text-muted-foreground text-xs">
                    You don&apos;t have <code>app.approve_deploy</code>; ask
                    an org admin to approve.
                  </p>
                )}
                <Button
                  asChild
                  variant="outline"
                  disabled={approveState.loading}
                >
                  <a href={`/deployments/${d.id}`}>View full detail</a>
                </Button>
              </div>
            </div>
          ) : alreadyDecided ? (
            <div className="bg-green-100 border border-green-300 rounded-md p-4 text-sm dark:bg-green-950/40 dark:border-green-900/60">
              <div className="flex items-center gap-2 font-medium">
                <CheckCircle2Icon className="size-4" /> Already approved
              </div>
              <p className="text-muted-foreground mt-1">
                The deployment moved past the approval gate. Status:{" "}
                <span className="capitalize">
                  {d.status.replace(/_/g, " ")}
                </span>
                .
              </p>
              <Button asChild variant="outline" size="sm" className="mt-3">
                <a href={`/deployments/${d.id}`}>View detail</a>
              </Button>
            </div>
          ) : failed ? (
            <div className="bg-red-100 border border-red-300 rounded-md p-4 text-sm dark:bg-red-950/40 dark:border-red-900/60">
              <div className="flex items-center gap-2 font-medium">
                <XCircleIcon className="size-4" /> Deployment failed
              </div>
              <p className="text-muted-foreground mt-1">
                Approval no longer applies. Inspect the lifecycle log on the
                detail page.
              </p>
              <Button asChild variant="outline" size="sm" className="mt-3">
                <a href={`/deployments/${d.id}`}>View detail</a>
              </Button>
            </div>
          ) : (
            <div className="bg-muted rounded-md p-4 text-sm">
              No approval action available in current state{" "}
              <span className="font-mono">{d.status}</span>.
            </div>
          )}
        </CardContent>
      </Card>

      <ConfirmDialog
        open={confirmApprove}
        onOpenChange={setConfirmApprove}
        title={`Approve deploy of ${d.imageTag || d.id}?`}
        description={`Unblocks the rollout to ${d.registeredAppSlug}/${d.environmentName}. The workflow resumes immediately and traffic shifts according to the configured strategy. Self-approval on a deploy you triggered is rejected by the backend.`}
        confirmLabel="Approve deployment"
        onConfirm={handleApprove}
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
      <dt className="text-muted-foreground text-xs uppercase tracking-wide">
        {label}
      </dt>
      <dd className={mono ? "font-mono text-sm" : "text-sm"}>{value}</dd>
    </div>
  );
}
