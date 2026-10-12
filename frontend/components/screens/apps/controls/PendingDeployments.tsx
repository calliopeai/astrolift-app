"use client";

import { CheckIcon, GitCommitIcon, Loader2Icon, ShieldCheckIcon, XIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Panel } from "@/components/panel/Panel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { AstroliftDeployment } from "@/graphql/lifecycle/lifecycle.types";
import { useFormatters } from "@/lib/i18n/formatters";

import { shortDeploymentTag, type usePendingDeployments } from "./use-pending-deployments";

export type PendingDeploymentsViewProps = ReturnType<typeof usePendingDeployments> & {
  /**
   * Show the queue only when it waits on the viewer (the Deployments tab):
   * someone who cannot approve finds these under the Waiting approval view.
   */
  onlyForApprovers?: boolean;
};

/**
 * Approval queue for deploys whose strategy requires sign-off before they
 * can roll out, as a panel (spec 44 §5.2). Hidden entirely when nothing's
 * waiting: operators see an approval prompt or nothing at all, no
 * "0 pending" busy-work card.
 */
export function PendingDeploymentsView({
  loading,
  pending,
  canApprove,
  onApprove,
  onReject,
  onlyForApprovers = false,
}: PendingDeploymentsViewProps) {
  const t = useTranslations("appControls.pendingDeployments");
  if (onlyForApprovers && !canApprove) return null;

  if (loading) {
    // Soft skeleton: most apps have none pending, so an aggressive
    // shimmer would be misleading. Tiny placeholder only.
    return <Skeleton className="h-12 w-full rounded-md" />;
  }

  if (pending.length === 0) return null;

  return (
    <Panel
      title={canApprove ? t("titleApprover") : t("title")}
      icon={<ShieldCheckIcon className="text-info-fg size-4" />}
      description={
        canApprove
          ? t("descriptionApprover")
          : t("description", { permission: "app.approve_deploy" })
      }
      actions={
        <>
          <span className="text-muted-foreground font-mono text-xs">{pending.length}</span>
          {canApprove ? (
            <Badge variant="secondary" className="text-2xs">
              {t("canApprove")}
            </Badge>
          ) : (
            <span className="text-muted-foreground text-2xs italic">{t("readOnly")}</span>
          )}
        </>
      }
      className="border-info-border"
    >
      <div className="flex min-w-0 flex-col gap-2">
        {pending.map((d) => (
          <PendingRow
            key={d.id}
            deployment={d}
            canApprove={canApprove}
            onApprove={() => onApprove(d)}
            onReject={(reason) => onReject(d, reason)}
          />
        ))}
      </div>
    </Panel>
  );
}

function PendingRow({
  deployment,
  canApprove,
  onApprove,
  onReject,
}: {
  deployment: AstroliftDeployment;
  canApprove: boolean;
  onApprove: () => Promise<void>;
  onReject: (reason: string) => Promise<void>;
}) {
  const fmt = useFormatters();
  const t = useTranslations("appControls.pendingDeployments");
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const [confirmApprove, setConfirmApprove] = useState(false);
  const [confirmReject, setConfirmReject] = useState(false);

  const shortTag = shortDeploymentTag(deployment);

  async function handleApprove() {
    setBusy("approve");
    try {
      await onApprove();
    } finally {
      setBusy(null);
    }
  }

  async function handleReject(reason: string) {
    setBusy("reject");
    try {
      await onReject(reason);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="bg-background flex flex-col gap-2 rounded-md border p-3 sm:flex-row sm:items-center">
      <div className="flex min-w-0 flex-1 items-center gap-3">
        <GitCommitIcon className="text-muted-foreground size-4 shrink-0" />
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <span className="font-mono text-sm">{shortTag}</span>
            {deployment.environmentName && (
              <span className="text-muted-foreground min-w-0 font-mono text-xs [overflow-wrap:anywhere]">
                → {deployment.environmentName}
              </span>
            )}
            <Badge variant="outline" className="text-2xs">
              {t("approvals", {
                received: deployment.approvalsReceived,
                required: deployment.approvalsRequired || 1,
              })}
            </Badge>
          </div>
          <div className="text-muted-foreground text-xs">
            {t("received", { when: fmt.formatRelativeTime(deployment.createdAt) })}
            {deployment.triggerKind && ` · ${deployment.triggerKind}`}
          </div>
        </div>
      </div>
      {canApprove && (
        // #420 Scope C — stack on mobile, full-width buttons + 44px min
        // tap target so on-call approvers don't mis-fire reject.
        <div className="flex shrink-0 flex-col gap-1.5 sm:flex-row sm:items-center">
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setConfirmReject(true)}
            disabled={busy !== null}
            className="text-muted-foreground hover:text-destructive min-h-11 w-full gap-1 sm:w-auto"
          >
            {busy === "reject" ? (
              <Loader2Icon className="size-3.5 animate-spin" />
            ) : (
              <XIcon className="size-3.5" />
            )}
            {t("reject")}
          </Button>
          <Button
            size="sm"
            onClick={() => setConfirmApprove(true)}
            disabled={busy !== null}
            className="min-h-11 w-full gap-1 sm:w-auto"
          >
            {busy === "approve" ? (
              <Loader2Icon className="size-3.5 animate-spin" />
            ) : (
              <CheckIcon className="size-3.5" />
            )}
            {t("approve")}
          </Button>
        </div>
      )}

      <ConfirmDialog
        open={confirmApprove}
        onOpenChange={setConfirmApprove}
        title={t("approveTitle", { tag: shortTag })}
        description={
          deployment.environmentName
            ? t("approveDescription", { environment: deployment.environmentName })
            : t("approveDescriptionDefault")
        }
        confirmLabel={t("approve")}
        onConfirm={handleApprove}
      />

      <ConfirmDialog
        reason={{
          label: t("rejectReason"),
          placeholder: t("rejectReasonPlaceholder"),
        }}
        open={confirmReject}
        onOpenChange={setConfirmReject}
        title={t("rejectTitle", { tag: shortTag })}
        description={t("rejectDescription")}
        confirmLabel={t("reject")}
        destructive
        onConfirm={handleReject}
      />
    </div>
  );
}
