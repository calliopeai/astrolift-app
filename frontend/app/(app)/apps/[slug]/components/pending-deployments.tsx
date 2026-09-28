"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { CheckIcon, GitCommitIcon, Loader2Icon, ShieldCheckIcon, XIcon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Skeleton } from "@/components/ui/skeleton";
import { useFormatters } from "@/lib/i18n/formatters";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";
import { ABORT_DEPLOYMENT, APPROVE_DEPLOYMENT } from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_DEPLOYMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftDeployment } from "@/graphql/lifecycle/lifecycle.types";
import type { MutationResult } from "@/graphql/identity/identity.types";

interface DeploymentsResp {
  astroliftDeployments: AstroliftDeployment[];
}

interface MutResp {
  approveDeployment?: MutationResult<AstroliftDeployment>;
  abortDeployment?: MutationResult<AstroliftDeployment>;
}

interface Props {
  appSlug: string;
}

/**
 * Approval queue for deploys whose strategy requires sign-off before they
 * can roll out. Hidden entirely when nothing's waiting — operators see an
 * approval prompt or nothing at all, no "0 pending" busy-work card.
 */
export function PendingDeployments({ appSlug }: Props) {
  const { can } = useMyPermissions();
  const canApprove = can("app.approve_deploy");

  const { data, loading, refetch } = useQuery<DeploymentsResp>(LIST_DEPLOYMENTS, {
    variables: { appSlug, limit: 25 },
    fetchPolicy: "cache-and-network",
    pollInterval: 30_000,
  });

  const pending = (data?.astroliftDeployments ?? []).filter((d) => d.status === "pending_approval");

  if (loading && !data) {
    // Soft skeleton — most apps have none pending, so an aggressive
    // shimmer would be misleading. Tiny placeholder only.
    return <Skeleton className="h-12 w-full rounded-md" />;
  }

  if (pending.length === 0) return null;

  return (
    <section className="border-info-border bg-info/5 flex flex-col gap-3 rounded-lg border p-4">
      <div className="flex items-center gap-2">
        <ShieldCheckIcon className="text-info-fg size-4" />
        <h2 className="text-sm font-semibold">Pending approval</h2>
        <span className="text-muted-foreground text-xs">({pending.length})</span>
        {canApprove ? (
          <Badge variant="secondary" className="text-2xs ml-auto">
            You can approve
          </Badge>
        ) : (
          <span className="text-muted-foreground text-2xs ml-auto italic">Read-only</span>
        )}
      </div>
      <p className="text-muted-foreground text-xs">
        {canApprove
          ? "Review each candidate before unlocking the rollout."
          : "Waiting on a reviewer with the `app.approve_deploy` permission."}
      </p>
      <div className="flex flex-col gap-2">
        {pending.map((d) => (
          <PendingRow
            key={d.id}
            deployment={d}
            canApprove={canApprove}
            onSettled={() => void refetch()}
          />
        ))}
      </div>
    </section>
  );
}

function PendingRow({
  deployment,
  canApprove,
  onSettled,
}: {
  deployment: AstroliftDeployment;
  canApprove: boolean;
  onSettled: () => void;
}) {
  const fmt = useFormatters();
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const [confirmApprove, setConfirmApprove] = useState(false);
  const [confirmReject, setConfirmReject] = useState(false);

  const [approve] = useMutation<MutResp>(APPROVE_DEPLOYMENT);
  const [abort] = useMutation<MutResp>(ABORT_DEPLOYMENT);

  const shortTag = (deployment.imageTag ?? deployment.id).slice(0, 10);

  async function handleApprove() {
    setBusy("approve");
    try {
      const { data } = await approve({
        variables: { input: { id: deployment.id } },
      });
      if (data?.approveDeployment?.ok) {
        toast.success(`Approved ${shortTag}.`);
      } else {
        throw new Error(data?.approveDeployment?.errors?.[0]?.message ?? "Approve failed.");
      }
    } finally {
      setBusy(null);
      onSettled();
    }
  }

  async function handleReject(reason: string) {
    setBusy("reject");
    try {
      const { data } = await abort({
        variables: { input: { id: deployment.id, reason } },
      });
      if (data?.abortDeployment?.ok) {
        toast.success("Deployment rejected.");
      } else {
        throw new Error(data?.abortDeployment?.errors?.[0]?.message ?? "Reject failed.");
      }
    } finally {
      setBusy(null);
      onSettled();
    }
  }

  return (
    <div className="bg-background flex flex-col gap-2 rounded-md border p-3 sm:flex-row sm:items-center">
      <div className="flex min-w-0 flex-1 items-center gap-3">
        <GitCommitIcon className="text-muted-foreground size-4 shrink-0" />
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex items-center gap-2">
            <span className="font-mono text-sm">{shortTag}</span>
            {deployment.environmentName && (
              <span className="text-muted-foreground text-xs">→ {deployment.environmentName}</span>
            )}
            <Badge variant="outline" className="text-2xs">
              {deployment.approvalsReceived}/{deployment.approvalsRequired || 1} approvals
            </Badge>
          </div>
          <div className="text-muted-foreground text-xs">
            Received {fmt.formatRelativeTime(deployment.createdAt)}
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
            Reject
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
            Approve &amp; deploy
          </Button>
        </div>
      )}

      <ConfirmDialog
        open={confirmApprove}
        onOpenChange={setConfirmApprove}
        title={`Approve ${shortTag}?`}
        description={`This unblocks the rollout to ${
          deployment.environmentName ?? "the target environment"
        }. Workflow resumes immediately — there is no way to pause it again before traffic shifts.`}
        confirmLabel="Approve & deploy"
        onConfirm={handleApprove}
      />

      <ConfirmDialog
        reason={{
          label: "Reason for rejection",
          placeholder:
            "Why is this deploy being rejected? Stored on the audit log + history sidebar.",
        }}
        open={confirmReject}
        onOpenChange={setConfirmReject}
        title={`Reject ${shortTag}?`}
        description="The pending deployment is aborted. CI must re-trigger to create a fresh deployment for review."
        confirmLabel="Reject"
        destructive
        onConfirm={handleReject}
      />
    </div>
  );
}
