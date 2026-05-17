"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  CheckCircle2Icon,
  ChevronDownIcon,
  ChevronUpIcon,
  ClockIcon,
  ExternalLinkIcon,
  GitCommitIcon,
  HistoryIcon,
  UserIcon,
  XCircleIcon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ConfirmDialogWithReason } from "@/components/ConfirmDialogWithReason";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  APPROVE_DEPLOYMENT,
  REJECT_DEPLOYMENT,
} from "@/graphql/lifecycle/lifecycle.mutations";
import {
  GET_DEPLOYMENT,
  GET_DEPLOYMENT_APPROVAL_HISTORY,
} from "@/graphql/lifecycle/lifecycle.queries";
import type {
  AstroliftDeployment,
  AstroliftDeploymentApprovalHistoryEntry,
  DeploymentStatus,
} from "@/graphql/lifecycle/lifecycle.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

interface MutationResultLite<T> {
  ok: boolean;
  errors: { code: string; message: string; field?: string | null }[];
  data: T | null;
}

interface DeploymentResp {
  astroliftDeployment: AstroliftDeployment | null;
}

interface ApprovalHistoryResp {
  astroliftDeploymentApprovalHistory: AstroliftDeploymentApprovalHistoryEntry[];
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

const COMMIT_PREVIEW_DEFAULT_LINES = 3;

function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}

export function ApprovalClient({ id }: { id: string }) {
  const t = useTranslations("lists.approval");
  const { can } = useMyPermissions();
  const { data, loading, refetch } = useQuery<DeploymentResp>(GET_DEPLOYMENT, {
    variables: { id },
    fetchPolicy: "cache-and-network",
  });
  const d = data?.astroliftDeployment ?? null;

  const [approve, approveState] = useMutation<{
    approveDeployment: MutationResultLite<AstroliftDeployment>;
  }>(APPROVE_DEPLOYMENT, {
    refetchQueries: [
      { query: GET_DEPLOYMENT, variables: { id } },
      { query: GET_DEPLOYMENT_APPROVAL_HISTORY, variables: { deploymentId: id } },
    ],
  });

  const [reject, rejectState] = useMutation<{
    rejectDeployment: MutationResultLite<AstroliftDeployment>;
  }>(REJECT_DEPLOYMENT, {
    refetchQueries: [
      { query: GET_DEPLOYMENT, variables: { id } },
      { query: GET_DEPLOYMENT_APPROVAL_HISTORY, variables: { deploymentId: id } },
    ],
  });

  const [confirmApprove, setConfirmApprove] = React.useState(false);
  const [confirmReject, setConfirmReject] = React.useState(false);

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

  const isSelfTrigger = d.triggeredByMe === true;
  const needsApproval =
    d.status === "pending_approval" &&
    d.approvalsReceived < d.approvalsRequired;
  const alreadyDecided =
    d.status === "deploying" ||
    d.status === "running" ||
    d.status === "rolled_back" ||
    d.status === "superseded";
  const failed = d.status === "failed";
  const canApprovePermission = can("app.approve_deploy");
  const showApproveCta = needsApproval && canApprovePermission && !isSelfTrigger;
  const showRejectCta = needsApproval && canApprovePermission && !isSelfTrigger;

  async function handleApprove() {
    const { data: result } = await approve({
      variables: { input: { id: d!.id } },
    });
    const r = result?.approveDeployment;
    if (r?.ok) {
      toast.success(t("toasts.approved", { status: r.data?.status ?? "ok" }));
      refetch().catch(() => {});
    } else {
      throw new Error(r?.errors[0]?.message ?? t("toasts.approveFailed"));
    }
  }

  async function handleReject(reason: string) {
    const { data: result } = await reject({
      variables: { input: { id: d!.id, reason } },
    });
    const r = result?.rejectDeployment;
    if (r?.ok) {
      toast.success(t("toasts.rejected"));
      refetch().catch(() => {});
    } else {
      throw new Error(r?.errors[0]?.message ?? t("toasts.rejectFailed"));
    }
  }

  return (
    <PageShell
      title={t("title")}
      description={`${d.registeredAppSlug} → ${d.environmentName}`}
    >
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2 flex flex-col gap-4">
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
                {isSelfTrigger && (
                  <SelfTriggerBadge label={t("selfTriggerBadge")} />
                )}
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

              <CommitProvenance deployment={d} t={t} />

              {d.abortedReason && (
                <div className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm">
                  <div className="text-destructive flex items-center gap-2 text-xs font-medium uppercase tracking-wide">
                    <XCircleIcon className="size-3.5" /> {t("rejectedReasonLabel")}
                  </div>
                  <p className="mt-1 whitespace-pre-wrap">{d.abortedReason}</p>
                </div>
              )}

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
                  <div className="mt-3 flex flex-wrap gap-2">
                    {!canApprovePermission ? (
                      <p className="text-muted-foreground text-xs">
                        {t("missingPermission")}
                      </p>
                    ) : isSelfTrigger ? (
                      <p className="text-muted-foreground text-xs">
                        {t("selfTriggerHelp")}
                      </p>
                    ) : (
                      <>
                        <Can permission="app.approve_deploy">
                          <Button
                            onClick={() => setConfirmApprove(true)}
                            disabled={approveState.loading || rejectState.loading}
                          >
                            <CheckCircle2Icon className="size-4" /> {t("approve")}
                          </Button>
                        </Can>
                        <Can permission="app.approve_deploy">
                          <Button
                            variant="destructive"
                            onClick={() => setConfirmReject(true)}
                            disabled={approveState.loading || rejectState.loading}
                          >
                            <XCircleIcon className="size-4" /> {t("reject")}
                          </Button>
                        </Can>
                      </>
                    )}
                    <Button
                      asChild
                      variant="outline"
                      disabled={approveState.loading || rejectState.loading}
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
        </div>

        <ApprovalHistoryPanel deploymentId={d.id} t={t} />
      </div>

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

      <ConfirmDialogWithReason
        open={confirmReject}
        onOpenChange={setConfirmReject}
        title={t("confirmReject.title", { tag: d.imageTag || d.id })}
        description={t("confirmReject.description", {
          app: d.registeredAppSlug,
          env: d.environmentName,
        })}
        reasonLabel={t("confirmReject.reasonLabel")}
        reasonPlaceholder={t("confirmReject.reasonPlaceholder")}
        reasonRequiredError={t("confirmReject.reasonRequired")}
        confirmLabel={t("confirmReject.confirm")}
        destructive
        onConfirm={handleReject}
      />
    </PageShell>
  );
}

function SelfTriggerBadge({ label }: { label: string }) {
  // No 'warning' badge variant exists — render an amber tone inline so
  // the visual weight matches a destructive badge without claiming
  // 'this is broken' (which destructive implies).
  return (
    <span
      data-slot="badge"
      className="h-5 inline-flex items-center gap-1 rounded-4xl border border-amber-400/60 bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-900 dark:bg-amber-950/40 dark:text-amber-200 dark:border-amber-700/60"
    >
      <AlertTriangleIcon className="size-3" />
      {label}
    </span>
  );
}

function CommitProvenance({
  deployment,
  t,
}: {
  deployment: AstroliftDeployment;
  t: ReturnType<typeof useTranslations>;
}) {
  // Hooks must run unconditionally — keep state declarations above the
  // early null return so React's hook ordering stays stable when a
  // deployment with no commit info flips into having one (subscription
  // refresh on a CI push).
  const [expanded, setExpanded] = React.useState(false);

  const hasCommitInfo =
    !!deployment.commitSha ||
    !!deployment.commitMessage ||
    !!deployment.commitAuthor;
  if (!hasCommitInfo) return null;

  const lines = (deployment.commitMessage || "").split(/\r?\n/);
  const truncatable = lines.length > COMMIT_PREVIEW_DEFAULT_LINES;
  const visibleLines = expanded
    ? lines
    : lines.slice(0, COMMIT_PREVIEW_DEFAULT_LINES);

  const shortSha = deployment.commitSha
    ? deployment.commitSha.slice(0, 7)
    : "";
  const commitUrl = buildCommitUrl(deployment.repoUrl, deployment.commitSha);

  return (
    <div className="rounded-md border bg-muted/30 p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <GitCommitIcon className="size-3.5" />
        {shortSha && (
          <code className="font-mono text-xs">{shortSha}</code>
        )}
        {deployment.branch && (
          <Badge variant="outline" className="text-[10px]">
            {deployment.branch}
          </Badge>
        )}
        {deployment.commitAuthor && (
          <span className="text-muted-foreground inline-flex items-center gap-1">
            <UserIcon className="size-3" />
            {deployment.commitAuthor}
          </span>
        )}
        {commitUrl && (
          <a
            href={commitUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="text-muted-foreground hover:text-foreground ml-auto inline-flex items-center gap-1 text-xs"
          >
            {t("commit.viewDiff")}
            <ExternalLinkIcon className="size-3" />
          </a>
        )}
      </div>
      {deployment.commitMessage ? (
        <>
          <pre className="bg-background mt-2 max-h-64 overflow-auto rounded p-2 font-mono text-xs leading-relaxed whitespace-pre-wrap">
            {visibleLines.join("\n")}
            {!expanded && truncatable ? "\n…" : ""}
          </pre>
          {truncatable && (
            <Button
              variant="ghost"
              size="sm"
              className="mt-1 h-6 text-xs"
              onClick={() => setExpanded((prev) => !prev)}
            >
              {expanded ? (
                <>
                  <ChevronUpIcon className="size-3" /> {t("commit.collapse")}
                </>
              ) : (
                <>
                  <ChevronDownIcon className="size-3" />{" "}
                  {t("commit.expand", { hidden: lines.length - COMMIT_PREVIEW_DEFAULT_LINES })}
                </>
              )}
            </Button>
          )}
        </>
      ) : (
        <p className="text-muted-foreground mt-2 text-xs italic">
          {t("commit.noMessage")}
        </p>
      )}
    </div>
  );
}

function buildCommitUrl(repoUrl: string, commitSha: string): string {
  if (!repoUrl || !commitSha) return "";
  // GitHub / Gitea / Codeberg / GitLab all use ``/commit/<sha>``; the
  // ones that don't (Bitbucket: ``/commits/<sha>``) the operator can
  // hand-edit. Default to the GH-shaped path; the repo-only link is
  // the next-best fallback so we always give the operator something.
  const trimmed = repoUrl.replace(/\.git$/, "").replace(/\/$/, "");
  return `${trimmed}/commit/${commitSha}`;
}

function ApprovalHistoryPanel({
  deploymentId,
  t,
}: {
  deploymentId: string;
  t: ReturnType<typeof useTranslations>;
}) {
  const [open, setOpen] = React.useState(true);
  const { data, loading } = useQuery<ApprovalHistoryResp>(
    GET_DEPLOYMENT_APPROVAL_HISTORY,
    {
      variables: { deploymentId },
      fetchPolicy: "cache-and-network",
    }
  );
  const entries = data?.astroliftDeploymentApprovalHistory ?? [];

  return (
    <Card className="lg:sticky lg:top-4 self-start">
      <CardHeader>
        <CardTitle className="flex items-center justify-between text-base">
          <span className="inline-flex items-center gap-2">
            <HistoryIcon className="size-4" />
            {t("history.title")}
          </span>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setOpen((prev) => !prev)}
            aria-expanded={open}
            aria-controls="approval-history-panel"
          >
            {open ? (
              <ChevronUpIcon className="size-4" />
            ) : (
              <ChevronDownIcon className="size-4" />
            )}
          </Button>
        </CardTitle>
      </CardHeader>
      {open && (
        <CardContent id="approval-history-panel" className="space-y-3">
          {loading && entries.length === 0 ? (
            <Skeleton className="h-16 w-full" />
          ) : entries.length === 0 ? (
            <p className="text-muted-foreground text-xs">
              {t("history.empty")}
            </p>
          ) : (
            <ul className="space-y-3">
              {entries.map((entry) => (
                <HistoryEntry key={entry.id} entry={entry} t={t} />
              ))}
            </ul>
          )}
        </CardContent>
      )}
    </Card>
  );
}

function HistoryEntry({
  entry,
  t,
}: {
  entry: AstroliftDeploymentApprovalHistoryEntry;
  t: ReturnType<typeof useTranslations>;
}) {
  const tone = decisionTone(entry.action, entry.decision);
  const Icon = decisionIcon(entry.action);
  return (
    <li className="flex gap-3 border-l-2 pl-3" style={{ borderColor: tone.border }}>
      <div className="mt-0.5">
        <Icon className="size-4" style={{ color: tone.icon }} />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-2 text-xs">
          <span className="font-medium">{t(`history.action.${actionKey(entry.action)}`)}</span>
          <span className="text-muted-foreground">·</span>
          <span className="text-muted-foreground">
            {entry.actorDisplay || entry.actorKind || t("history.actorUnknown")}
          </span>
        </div>
        <p className="text-muted-foreground text-[11px]">
          {new Date(entry.occurredAt).toLocaleString()}
        </p>
        {entry.reason && (
          <p className="mt-1 whitespace-pre-wrap text-xs">{entry.reason}</p>
        )}
      </div>
    </li>
  );
}

function actionKey(action: string): string {
  switch (action) {
    case "deployment.start":
      return "started";
    case "deployment.approve":
    case "deployment.approve_by_token":
      return "approved";
    case "deployment.reject":
    case "deployment.reject_by_token":
      return "rejected";
    case "deployment.abort":
      return "aborted";
    default:
      return "other";
  }
}

interface ToneCss {
  border: string;
  icon: string;
}

function decisionTone(action: string, decision: string): ToneCss {
  if (
    action === "deployment.approve" ||
    action === "deployment.approve_by_token"
  ) {
    return { border: "rgb(34 197 94 / 0.5)", icon: "rgb(34 197 94)" };
  }
  if (
    action === "deployment.reject" ||
    action === "deployment.reject_by_token" ||
    action === "deployment.abort"
  ) {
    return { border: "rgb(239 68 68 / 0.5)", icon: "rgb(239 68 68)" };
  }
  if (decision === "DENY") {
    return { border: "rgb(239 68 68 / 0.5)", icon: "rgb(239 68 68)" };
  }
  return { border: "rgb(148 163 184 / 0.5)", icon: "rgb(100 116 139)" };
}

function decisionIcon(action: string) {
  if (
    action === "deployment.approve" ||
    action === "deployment.approve_by_token"
  ) {
    return CheckCircle2Icon;
  }
  if (
    action === "deployment.reject" ||
    action === "deployment.reject_by_token" ||
    action === "deployment.abort"
  ) {
    return XCircleIcon;
  }
  return ClockIcon;
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
