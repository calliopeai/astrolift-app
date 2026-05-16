"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { CheckCircle2Icon, ClockIcon, XCircleIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
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
  const t = useTranslations("lists.approval");
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

  const [confirmApprove, setConfirmApprove] = React.useState(false);

  if (loading && !d) {
    return (
      <PageShell title={t("title")} description={t("loading")}>
        <Skeleton className="h-32 w-full" />
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
            {t("expired")}{" "}
            <Link href="/deployments" className="underline">
              {t("deploymentsList")}
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
      title={t("title")}
      description={`${d.registeredAppSlug} → ${d.environmentName}`}
    >
      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center gap-3">
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
        <CardContent className="space-y-4">
          <dl className="grid grid-cols-2 gap-x-8 gap-y-2 text-sm">
            <Field label={t("fields.imageTag")} mono value={d.imageTag || "—"} />
            <Field
              label={t("fields.imageDigest")}
              mono
              value={d.imageDigest || "—"}
            />
            <Field
              label={t("fields.clusterRevision")}
              mono
              value={d.clusterRevision || "—"}
            />
            <Field
              label={t("fields.workload")}
              mono
              value={d.workloadSlug || "—"}
            />
            <Field label={t("fields.created")} value={formatTime(d.createdAt)} />
            <Field label={t("fields.started")} value={formatTime(d.startedAt)} />
          </dl>

          {needsApproval ? (
            <div className="bg-amber-100 border border-amber-300 rounded-md p-4 text-sm dark:bg-amber-950/40 dark:border-amber-900/60">
              <div className="flex items-center gap-2 font-medium">
                <ClockIcon className="size-4" /> {t("awaiting")}
              </div>
              <p className="text-muted-foreground mt-1">
                {t("awaitingDesc", {
                  remaining: d.approvalsRequired - d.approvalsReceived,
                })}
              </p>
              <div className="mt-3 flex gap-2">
                {can("app.approve_deploy") ? (
                  <Can permission="app.approve_deploy">
                    <Button
                      onClick={() => setConfirmApprove(true)}
                      disabled={approveState.loading}
                    >
                      <CheckCircle2Icon className="size-4" /> {t("approve")}
                    </Button>
                  </Can>
                ) : (
                  <p className="text-muted-foreground text-xs">
                    {t("missingPermission")}
                  </p>
                )}
                <Button
                  asChild
                  variant="outline"
                  disabled={approveState.loading}
                >
                  <a href={`/deployments/${d.id}`}>{t("viewDetail")}</a>
                </Button>
              </div>
            </div>
          ) : alreadyDecided ? (
            <div className="bg-green-100 border border-green-300 rounded-md p-4 text-sm dark:bg-green-950/40 dark:border-green-900/60">
              <div className="flex items-center gap-2 font-medium">
                <CheckCircle2Icon className="size-4" /> {t("alreadyApproved")}
              </div>
              <p className="text-muted-foreground mt-1">
                {t("alreadyApprovedDesc", { status: d.status.replace(/_/g, " ") })}
              </p>
              <Button asChild variant="outline" size="sm" className="mt-3">
                <a href={`/deployments/${d.id}`}>{t("viewDetailShort")}</a>
              </Button>
            </div>
          ) : failed ? (
            <div className="bg-red-100 border border-red-300 rounded-md p-4 text-sm dark:bg-red-950/40 dark:border-red-900/60">
              <div className="flex items-center gap-2 font-medium">
                <XCircleIcon className="size-4" /> {t("failed")}
              </div>
              <p className="text-muted-foreground mt-1">{t("failedDesc")}</p>
              <Button asChild variant="outline" size="sm" className="mt-3">
                <a href={`/deployments/${d.id}`}>{t("viewDetailShort")}</a>
              </Button>
            </div>
          ) : (
            <div className="bg-muted rounded-md p-4 text-sm">
              {t("noAction", { status: d.status })}
            </div>
          )}
        </CardContent>
      </Card>

      <ConfirmDialog
        open={confirmApprove}
        onOpenChange={setConfirmApprove}
        title={t("confirm.title", { tag: d.imageTag || d.id })}
        description={t("confirm.description", {
          app: d.registeredAppSlug,
          env: d.environmentName,
        })}
        confirmLabel={t("confirm.confirm")}
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
