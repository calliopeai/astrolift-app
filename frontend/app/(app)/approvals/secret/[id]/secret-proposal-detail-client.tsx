"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { CheckIcon, Loader2Icon, XCircleIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ConfirmDialogWithReason } from "@/components/ConfirmDialogWithReason";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Section } from "@/components/ui/section";
import { Skeleton } from "@/components/ui/skeleton";
import {
  APPROVE_SECRET_CHANGE,
  REJECT_SECRET_CHANGE,
  WITHDRAW_SECRET_CHANGE,
} from "@/graphql/services/services.mutations";
import { GET_SECRET_CHANGE_PROPOSAL } from "@/graphql/services/services.queries";
import type { AstroliftSecretChangeProposal } from "@/graphql/services/services.types";
import { useFormatters } from "@/lib/i18n/formatters";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

interface GetResp {
  astroliftSecretChangeProposal: AstroliftSecretChangeProposal | null;
}

interface MutationResultLite<T> {
  ok: boolean;
  errors: { code: string; message: string; field?: string | null }[];
  data: T | null;
}

interface ApproveResp {
  approveSecretChange: MutationResultLite<AstroliftSecretChangeProposal>;
}
interface RejectResp {
  rejectSecretChange: MutationResultLite<AstroliftSecretChangeProposal>;
}
interface WithdrawResp {
  withdrawSecretChange: MutationResultLite<AstroliftSecretChangeProposal>;
}

/**
 * Detail view for a secret-change proposal (#488). Side-by-side diff
 * of current → proposed, the approver vote list, and the operator
 * affordances (approve / reject / withdraw). Values are masked at
 * render — explicit reveal lives behind the per-row revealAppSecret
 * mutation (#424) and is out of scope on the proposal page.
 */
export function SecretProposalDetailClient({ proposalId }: { proposalId: string }) {
  const t = useTranslations("approvals.secretProposalDetail");
  const fmt = useFormatters();
  const router = useRouter();
  const { can } = useMyPermissions();
  const canApprove = can("secret.approve");

  const { data, loading, refetch } = useQuery<GetResp>(GET_SECRET_CHANGE_PROPOSAL, {
    variables: { id: proposalId },
    fetchPolicy: "cache-and-network",
  });

  const proposal = data?.astroliftSecretChangeProposal ?? null;

  const [confirmApprove, setConfirmApprove] = React.useState(false);
  const [confirmReject, setConfirmReject] = React.useState(false);
  const [confirmWithdraw, setConfirmWithdraw] = React.useState(false);

  const [approve, approveState] = useMutation<ApproveResp>(APPROVE_SECRET_CHANGE, {
    refetchQueries: [{ query: GET_SECRET_CHANGE_PROPOSAL, variables: { id: proposalId } }],
  });
  const [reject, rejectState] = useMutation<RejectResp>(REJECT_SECRET_CHANGE, {
    refetchQueries: [{ query: GET_SECRET_CHANGE_PROPOSAL, variables: { id: proposalId } }],
  });
  const [withdraw, withdrawState] = useMutation<WithdrawResp>(WITHDRAW_SECRET_CHANGE, {
    refetchQueries: [{ query: GET_SECRET_CHANGE_PROPOSAL, variables: { id: proposalId } }],
  });

  const busy = approveState.loading || rejectState.loading || withdrawState.loading;

  async function handleApprove() {
    const { data } = await approve({
      variables: { input: { proposalId } },
    });
    const result = data?.approveSecretChange;
    if (!result?.ok) {
      throw new Error(result?.errors[0]?.message ?? t("toasts.approveFailed"));
    }
    toast.success(t("toasts.approveOk"));
    refetch().catch(() => {});
  }

  async function handleReject(reason: string) {
    const { data } = await reject({
      variables: { input: { proposalId, reason } },
    });
    const result = data?.rejectSecretChange;
    if (!result?.ok) {
      throw new Error(result?.errors[0]?.message ?? t("toasts.rejectFailed"));
    }
    toast.success(t("toasts.rejectOk"));
    refetch().catch(() => {});
  }

  async function handleWithdraw() {
    const { data } = await withdraw({ variables: { input: { proposalId } } });
    const result = data?.withdrawSecretChange;
    if (!result?.ok) {
      throw new Error(result?.errors[0]?.message ?? t("toasts.withdrawFailed"));
    }
    toast.success(t("toasts.withdrawOk"));
    router.push("/approvals");
  }

  if (loading && !data) {
    return (
      <PageShell title={t("loading.title")}>
        <Skeleton className="h-32 w-full" />
      </PageShell>
    );
  }

  if (proposal === null) {
    return (
      <PageShell title={t("notFound.title")} description={t("notFound.description")}>
        <p className="text-muted-foreground text-sm">{t("notFound.description")}</p>
      </PageShell>
    );
  }

  const env = proposal.environmentName || t("appWide");
  const summary =
    (proposal.payloadDiff as { summary?: string })?.summary ?? `${proposal.op} on ${env}`;
  const isPending = proposal.status === "pending";

  return (
    <PageShell title={t("title", { app: proposal.registeredAppSlug, env })} description={summary}>
      <Card>
        <CardContent className="flex flex-col gap-3 p-4">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline">{proposal.status}</Badge>
            <Badge variant="outline">{t("opLabel", { op: proposal.op })}</Badge>
            <Badge variant="outline">
              {t("approvalsCount", {
                received: proposal.approvalsCount,
                required: proposal.requiredApproverCount,
              })}
            </Badge>
            <span className="text-muted-foreground text-xs">
              {t("proposedBy", {
                name: proposal.proposerDisplayName || t("unknownProposer"),
                rel: fmt.formatRelativeTime(proposal.createdAt),
              })}
            </span>
          </div>
          {isPending && (
            <p className="text-muted-foreground text-xs">
              {t("expiresIn", {
                rel: fmt.formatRelativeTime(proposal.expiresAt),
              })}
            </p>
          )}
          {proposal.applyError && (
            <p className="text-destructive text-xs">
              {t("applyError", { message: proposal.applyError })}
            </p>
          )}
        </CardContent>
      </Card>

      <DiffPanel proposal={proposal} />

      <ApproverList proposal={proposal} />

      {isPending && (
        <div className="bg-background fixed inset-x-0 bottom-0 z-30 border-t shadow-lg">
          <div className="mx-auto flex max-w-5xl flex-col items-stretch gap-2 p-3 sm:flex-row sm:items-center sm:justify-end">
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => setConfirmWithdraw(true)}
              className="min-h-11 w-full sm:w-auto"
            >
              {withdrawState.loading ? <Loader2Icon className="size-4 animate-spin" /> : null}
              {t("withdraw")}
            </Button>
            {canApprove && (
              <>
                <Button
                  variant="destructive"
                  disabled={busy}
                  onClick={() => setConfirmReject(true)}
                  className="min-h-11 w-full sm:w-auto"
                >
                  {rejectState.loading ? (
                    <Loader2Icon className="size-4 animate-spin" />
                  ) : (
                    <XCircleIcon className="size-4" />
                  )}
                  {t("reject")}
                </Button>
                <Button
                  disabled={busy}
                  onClick={() => setConfirmApprove(true)}
                  className="min-h-11 w-full sm:w-auto"
                >
                  {approveState.loading ? (
                    <Loader2Icon className="size-4 animate-spin" />
                  ) : (
                    <CheckIcon className="size-4" />
                  )}
                  {t("approve")}
                </Button>
              </>
            )}
          </div>
        </div>
      )}

      <ConfirmDialog
        open={confirmApprove}
        onOpenChange={setConfirmApprove}
        title={t("confirmApprove.title")}
        description={t("confirmApprove.description")}
        confirmLabel={t("confirmApprove.confirm")}
        onConfirm={handleApprove}
      />
      <ConfirmDialogWithReason
        open={confirmReject}
        onOpenChange={setConfirmReject}
        title={t("confirmReject.title")}
        description={t("confirmReject.description")}
        reasonLabel={t("confirmReject.reasonLabel")}
        reasonPlaceholder={t("confirmReject.reasonPlaceholder")}
        reasonRequiredError={t("confirmReject.reasonRequired")}
        confirmLabel={t("confirmReject.confirm")}
        destructive
        onConfirm={handleReject}
      />
      <ConfirmDialog
        open={confirmWithdraw}
        onOpenChange={setConfirmWithdraw}
        title={t("confirmWithdraw.title")}
        description={t("confirmWithdraw.description")}
        confirmLabel={t("confirmWithdraw.confirm")}
        onConfirm={handleWithdraw}
      />
    </PageShell>
  );
}

function DiffPanel({ proposal }: { proposal: AstroliftSecretChangeProposal }) {
  const t = useTranslations("approvals.secretProposalDetail.diff");
  const diff = (proposal.payloadDiff ?? {}) as {
    op?: string;
    before?: Record<string, unknown>;
    after?: Record<string, unknown>;
    summary?: string;
  };
  const before = diff.before ?? {};
  const after = diff.after ?? {};

  return (
    <Section title={t("title")}>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="rounded-md border p-3">
          <h3 className="mb-2 text-sm font-medium">{t("before")}</h3>
          <DiffPairs entries={before} />
        </div>
        <div className="rounded-md border p-3">
          <h3 className="mb-2 text-sm font-medium">{t("after")}</h3>
          <DiffPairs entries={after} />
        </div>
      </div>
      <p className="text-muted-foreground text-xs">{t("maskNote")}</p>
    </Section>
  );
}

function DiffPairs({ entries }: { entries: Record<string, unknown> }) {
  const keys = Object.keys(entries);
  if (keys.length === 0) {
    return <p className="text-muted-foreground text-xs">—</p>;
  }
  return (
    <dl className="space-y-1 text-sm">
      {keys.map((key) => (
        <div key={key} className="flex gap-2">
          <dt className="text-muted-foreground font-mono text-xs">{key}</dt>
          <dd className="font-mono break-all">{String(entries[key] ?? "")}</dd>
        </div>
      ))}
    </dl>
  );
}

function ApproverList({ proposal }: { proposal: AstroliftSecretChangeProposal }) {
  const t = useTranslations("approvals.secretProposalDetail.approvers");
  if (proposal.approvals.length === 0) {
    return (
      <Section title={t("title")}>
        <p className="text-muted-foreground text-sm">{t("empty")}</p>
      </Section>
    );
  }
  return (
    <Section title={t("title")}>
      <ul className="divide-y">
        {proposal.approvals.map((approval) => (
          <li
            key={approval.id}
            className="flex flex-col gap-1 py-2 text-sm first:pt-0 last:pb-0 sm:flex-row sm:items-center sm:justify-between"
          >
            <span>
              <strong>{approval.approverDisplayName || t("unknownApprover")}</strong>
              <Badge
                variant={approval.decision === "approved" ? "outline" : "destructive"}
                className="ml-2"
              >
                {approval.decision}
              </Badge>
            </span>
            {approval.reason && (
              <span className="text-muted-foreground text-xs">{approval.reason}</span>
            )}
          </li>
        ))}
      </ul>
    </Section>
  );
}
