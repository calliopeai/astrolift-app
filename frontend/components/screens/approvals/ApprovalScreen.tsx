"use client";

import {
  AlertTriangleIcon,
  CheckCircle2Icon,
  ChevronDownIcon,
  ChevronUpIcon,
  ClockIcon,
  ExternalLinkIcon,
  GitCommitIcon,
  UserIcon,
  XCircleIcon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DeploymentStatusPill } from "@/components/DeploymentStatusPill";
import { PageShell } from "@/components/PageShell";
import { QuorumWidget } from "@/components/QuorumWidget";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DefinitionList } from "@/components/ui/definition-list";
import { Skeleton } from "@/components/ui/skeleton";
import type { AstroliftDeployment, DeploymentStatus } from "@/graphql/lifecycle/lifecycle.types";
import { useFormatters } from "@/lib/i18n/formatters";

import type { useApproval } from "./use-approval";

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

const COMMIT_PREVIEW_DEFAULT_LINES = 3;

export type ApprovalScreenProps = ReturnType<typeof useApproval> & {
  /** The approval history side panel; its query runs only once the deployment is known. */
  history?: React.ReactNode;
};

/**
 * Single-deployment approval page: status, provenance, the approve /
 * reject CTAs (hidden for self-triggered deploys and approvers without
 * `app.approve_deploy`), and the approval history panel.
 */
export function ApprovalScreen({
  deployment: d,
  loading,
  canApprovePermission,
  busy,
  onApprove,
  onReject,
  history,
}: ApprovalScreenProps) {
  const t = useTranslations("lists.approval");
  const fmt = useFormatters();
  const formatTime = (iso: string | null | undefined): string =>
    iso ? fmt.formatDateTime(iso) : "—";

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
      <PageShell title={t("notFoundTitle")} description={t("notFoundDescription")}>
        <Card>
          <CardContent className="text-muted-foreground p-6 text-sm">
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
    d.status === "pending_approval" && d.approvalsReceived < d.approvalsRequired;
  const alreadyDecided =
    d.status === "deploying" ||
    d.status === "running" ||
    d.status === "rolled_back" ||
    d.status === "superseded";
  const failed = d.status === "failed";
  const showApproveCta = needsApproval && canApprovePermission && !isSelfTrigger;
  const showRejectCta = needsApproval && canApprovePermission && !isSelfTrigger;

  return (
    <PageShell title={t("title")} description={`${d.registeredAppSlug} → ${d.environmentName}`}>
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="flex flex-col gap-4 lg:col-span-2">
          <Card>
            <CardHeader>
              <CardTitle className="flex flex-wrap items-center gap-3">
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
                {isSelfTrigger && <SelfTriggerBadge label={t("selfTriggerBadge")} />}
              </CardTitle>
              {/* #420 — quorum widget replaces the count-only badge for
                  gated deploys. Renders nothing when requiredApproverCount===0
                  so non-gated views stay clean. */}
              <QuorumWidget
                requiredApproverCount={d.requiredApproverCount ?? d.approvalsRequired}
                approvedBy={d.approvedBy ?? []}
                awaitingApprovers={d.awaitingApprovers ?? []}
              />
            </CardHeader>
            <CardContent className="space-y-4">
              <DefinitionList
                items={[
                  {
                    term: t("fields.imageTag"),
                    description: <span className="font-mono">{d.imageTag || "—"}</span>,
                  },
                  {
                    term: t("fields.imageDigest"),
                    description: <span className="font-mono">{d.imageDigest || "—"}</span>,
                  },
                  {
                    term: t("fields.clusterRevision"),
                    description: <span className="font-mono">{d.clusterRevision || "—"}</span>,
                  },
                  {
                    term: t("fields.workload"),
                    description: <span className="font-mono">{d.workloadSlug || "—"}</span>,
                  },
                  { term: t("fields.created"), description: formatTime(d.createdAt) },
                  { term: t("fields.started"), description: formatTime(d.startedAt) },
                ]}
              />

              <CommitProvenance deployment={d} t={t} />

              {d.abortedReason && (
                <div className="border-destructive/40 bg-destructive/5 rounded-md border p-3 text-sm">
                  <div className="text-destructive flex items-center gap-2 text-xs font-medium tracking-wide uppercase">
                    <XCircleIcon className="size-3.5" /> {t("rejectedReasonLabel")}
                  </div>
                  <p className="mt-1 whitespace-pre-wrap">{d.abortedReason}</p>
                </div>
              )}

              {needsApproval ? (
                <div className="border-warning-border bg-warning/10 rounded-md border p-4 text-sm">
                  <div className="flex items-center gap-2 font-medium">
                    <ClockIcon className="size-4" /> {t("awaiting")}
                  </div>
                  <p className="text-muted-foreground mt-1">
                    {t("awaitingDesc", {
                      remaining: d.approvalsRequired - d.approvalsReceived,
                    })}
                  </p>
                  {/* #420 Scope C — mobile-friendly: stack column-by-column
                      below sm:, expand to full width per row, lock buttons at
                      44px min so on-call approvers don't mis-tap. */}
                  <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:flex-wrap">
                    {!canApprovePermission ? (
                      <p className="text-muted-foreground text-xs">{t("missingPermission")}</p>
                    ) : isSelfTrigger ? (
                      <p className="text-muted-foreground text-xs">{t("selfTriggerHelp")}</p>
                    ) : (
                      <>
                        <Can permission="app.approve_deploy">
                          <Button
                            onClick={() => setConfirmApprove(true)}
                            disabled={busy}
                            className="min-h-11 w-full sm:w-auto"
                          >
                            <CheckCircle2Icon className="size-4" /> {t("approve")}
                          </Button>
                        </Can>
                        <Can permission="app.approve_deploy">
                          <Button
                            variant="destructive"
                            onClick={() => setConfirmReject(true)}
                            disabled={busy}
                            className="min-h-11 w-full sm:w-auto"
                          >
                            <XCircleIcon className="size-4" /> {t("reject")}
                          </Button>
                        </Can>
                      </>
                    )}
                    <Button
                      asChild
                      variant="outline"
                      disabled={busy}
                      className="min-h-11 w-full sm:w-auto"
                    >
                      <a href={`/deployments/${d.id}`}>{t("viewDetail")}</a>
                    </Button>
                  </div>
                </div>
              ) : alreadyDecided ? (
                <div className="border-success-border bg-success/10 rounded-md border p-4 text-sm">
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
                <div className="border-danger-border bg-danger/10 rounded-md border p-4 text-sm">
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

        {history}
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
        onConfirm={onApprove}
      />

      <ConfirmDialog
        reason={{
          label: t("confirmReject.reasonLabel"),
          placeholder: t("confirmReject.reasonPlaceholder"),
          requiredError: t("confirmReject.reasonRequired"),
        }}
        open={confirmReject}
        onOpenChange={setConfirmReject}
        title={t("confirmReject.title", { tag: d.imageTag || d.id })}
        description={t("confirmReject.description", {
          app: d.registeredAppSlug,
          env: d.environmentName,
        })}
        confirmLabel={t("confirmReject.confirm")}
        destructive
        onConfirm={onReject}
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
      className="border-warning-border bg-warning/10 text-warning-fg inline-flex h-5 items-center gap-1 rounded-4xl border px-2 py-0.5 text-xs font-medium"
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
    !!deployment.commitSha || !!deployment.commitMessage || !!deployment.commitAuthor;
  if (!hasCommitInfo) return null;

  const lines = (deployment.commitMessage || "").split(/\r?\n/);
  const truncatable = lines.length > COMMIT_PREVIEW_DEFAULT_LINES;
  const visibleLines = expanded ? lines : lines.slice(0, COMMIT_PREVIEW_DEFAULT_LINES);

  const shortSha = deployment.commitSha ? deployment.commitSha.slice(0, 7) : "";
  const commitUrl = buildCommitUrl(deployment.repoUrl, deployment.commitSha);

  return (
    <div className="text-sm">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <GitCommitIcon className="size-3.5" />
        {shortSha && <code className="font-mono text-xs">{shortSha}</code>}
        {deployment.branch && (
          <Badge variant="outline" className="text-2xs">
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
        <p className="text-muted-foreground mt-2 text-xs italic">{t("commit.noMessage")}</p>
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
