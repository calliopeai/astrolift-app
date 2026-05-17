"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  CheckIcon,
  GitCommitIcon,
  Loader2Icon,
  ShieldCheckIcon,
  XCircleIcon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ConfirmDialogWithReason } from "@/components/ConfirmDialogWithReason";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  BULK_APPROVE_DEPLOYMENTS,
  BULK_REJECT_DEPLOYMENTS,
} from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_DEPLOYMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type {
  AstroliftBulkDeploymentResultData,
  AstroliftDeployment,
} from "@/graphql/lifecycle/lifecycle.types";
import { useFormatters } from "@/lib/i18n/formatters";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

interface ListResp {
  astroliftDeployments: AstroliftDeployment[];
}

interface MutationResultLite<T> {
  ok: boolean;
  errors: { code: string; message: string; field?: string | null }[];
  data: T | null;
}

interface BulkApproveResp {
  bulkApproveDeployments: MutationResultLite<AstroliftBulkDeploymentResultData>;
}

interface BulkRejectResp {
  bulkRejectDeployments: MutationResultLite<AstroliftBulkDeploymentResultData>;
}

/**
 * Global approvals queue (#420). Lists every pending_approval deploy
 * across the org. Selection-driven bulk approve / reject lives in a
 * sticky footer that appears when at least one row is checked.
 *
 * Bulk is restricted to same-env: different envs may have different
 * approver policies (env A might require 2 sign-offs, env B might
 * allow 1), so a cross-env bulk would silently fan into mixed
 * outcomes. The footer's CTA disables itself with an inline hint when
 * the current selection spans more than one env.
 */
export function ApprovalsQueueClient() {
  const t = useTranslations("lists.approvalsQueue");
  const fmt = useFormatters();
  const { can } = useMyPermissions();
  const canApprove = can("app.approve_deploy");

  const { data, loading, refetch } = useQuery<ListResp>(LIST_DEPLOYMENTS, {
    variables: { limit: 100 },
    fetchPolicy: "cache-and-network",
    pollInterval: 30_000,
  });

  const pending = (data?.astroliftDeployments ?? []).filter(
    (d) => d.status === "pending_approval",
  );

  const [selected, setSelected] = React.useState<Set<string>>(new Set());

  // Drop selections for rows that disappeared from the queue between
  // polls (got approved/rejected by someone else). Computed in
  // ``selectedDeploys`` rather than reconciled via a side-effecting
  // ``useEffect``: the queue list is the source of truth, the
  // ``selected`` Set is the intent — taking the intersection at render
  // time avoids the setState-in-effect anti-pattern flagged by
  // react-hooks/set-state-in-effect.
  const selectedDeploys = React.useMemo(
    () => pending.filter((d) => selected.has(d.id)),
    [pending, selected],
  );

  // Same-env constraint: bulk CTA disables when the selection spans
  // more than one (app, env) pair OR more than one env name across
  // apps. Per-app pairing matters because the approver policy is
  // declared on (RegisteredApp, AppEnvironment).
  const selectedEnvKeys = React.useMemo(() => {
    const keys = new Set<string>();
    for (const d of selectedDeploys) {
      keys.add(`${d.registeredAppSlug}::${d.environmentName}`);
    }
    return keys;
  }, [selectedDeploys]);
  const crossEnvSelection = selectedEnvKeys.size > 1;
  const bulkDisabled = selectedDeploys.length === 0 || crossEnvSelection;

  const [confirmApprove, setConfirmApprove] = React.useState(false);
  const [confirmReject, setConfirmReject] = React.useState(false);

  const [bulkApprove, bulkApproveState] = useMutation<BulkApproveResp>(
    BULK_APPROVE_DEPLOYMENTS,
    {
      refetchQueries: [{ query: LIST_DEPLOYMENTS, variables: { limit: 100 } }],
    },
  );
  const [bulkReject, bulkRejectState] = useMutation<BulkRejectResp>(
    BULK_REJECT_DEPLOYMENTS,
    {
      refetchQueries: [{ query: LIST_DEPLOYMENTS, variables: { limit: 100 } }],
    },
  );

  const busy = bulkApproveState.loading || bulkRejectState.loading;

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }

  function toggleAllVisible() {
    setSelected((prev) => {
      if (prev.size === pending.length && pending.length > 0) {
        return new Set();
      }
      return new Set(pending.map((d) => d.id));
    });
  }

  function reportBulkResult(
    label: string,
    data: AstroliftBulkDeploymentResultData | null | undefined,
  ) {
    if (!data) {
      throw new Error(t("toasts.unexpected"));
    }
    if (data.failedCount === 0) {
      toast.success(t("toasts.allOk", { label, count: data.succeededCount }));
    } else if (data.succeededCount === 0) {
      throw new Error(
        t("toasts.allFailed", {
          label,
          count: data.failedCount,
          message: data.results[0]?.errors[0]?.message ?? "",
        }),
      );
    } else {
      toast.warning(
        t("toasts.partial", {
          label,
          succeeded: data.succeededCount,
          failed: data.failedCount,
        }),
      );
    }
  }

  async function handleBulkApprove() {
    const ids = selectedDeploys.map((d) => d.id);
    const { data } = await bulkApprove({ variables: { input: { deploymentIds: ids } } });
    const result = data?.bulkApproveDeployments;
    if (!result?.ok) {
      throw new Error(result?.errors[0]?.message ?? t("toasts.approveFailed"));
    }
    reportBulkResult(t("approveLabel"), result.data);
    setSelected(new Set());
    refetch().catch(() => {});
  }

  async function handleBulkReject(reason: string) {
    const ids = selectedDeploys.map((d) => d.id);
    const { data } = await bulkReject({
      variables: { input: { deploymentIds: ids, reason } },
    });
    const result = data?.bulkRejectDeployments;
    if (!result?.ok) {
      throw new Error(result?.errors[0]?.message ?? t("toasts.rejectFailed"));
    }
    reportBulkResult(t("rejectLabel"), result.data);
    setSelected(new Set());
    refetch().catch(() => {});
  }

  if (loading && !data) {
    return (
      <PageShell title={t("title")} description={t("description")}>
        <Skeleton className="h-24 w-full" />
      </PageShell>
    );
  }

  if (pending.length === 0) {
    return (
      <PageShell title={t("title")} description={t("description")}>
        <EmptyState
          title={t("empty.title")}
          description={t("empty.description")}
          icon={<ShieldCheckIcon className="size-6" aria-hidden />}
        />
      </PageShell>
    );
  }

  const summaryApps = Array.from(
    new Set(selectedDeploys.map((d) => `${d.registeredAppSlug}/${d.environmentName}`)),
  ).join(", ");

  return (
    <PageShell title={t("title")} description={t("description")}>
      {/* Bulk-CTA controls. Visible only when something is selected. */}
      <div className="bg-card flex items-center justify-between gap-2 rounded-md border p-2 text-sm">
        <div className="flex items-center gap-2">
          <input
            type="checkbox"
            aria-label={t("selectAllLabel")}
            checked={selected.size === pending.length && pending.length > 0}
            onChange={toggleAllVisible}
            className="size-4"
          />
          <span className="text-muted-foreground">
            {t("selectionStatus", { selected: selected.size, total: pending.length })}
          </span>
        </div>
        {!canApprove && (
          <span className="text-muted-foreground text-xs italic">{t("readOnlyHint")}</span>
        )}
      </div>

      <ul className="mt-3 flex flex-col gap-2 pb-24">
        {pending.map((deployment) => (
          <li key={deployment.id}>
            <QueueRow
              deployment={deployment}
              checked={selected.has(deployment.id)}
              onToggle={() => toggle(deployment.id)}
              canApprove={canApprove}
              formatRelative={fmt.formatRelativeTime}
            />
          </li>
        ))}
      </ul>

      {selectedDeploys.length > 0 && canApprove && (
        // Sticky bottom action bar — surfaces only when a selection is
        // active. Same-env constraint is enforced inline here so the
        // operator sees the reason the CTA is disabled.
        <div className="bg-background pointer-events-auto fixed inset-x-0 bottom-0 z-30 border-t shadow-lg">
          <div className="mx-auto flex max-w-5xl flex-col items-stretch gap-2 p-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="text-sm">
              <p className="font-medium">
                {t("footer.selected", { count: selectedDeploys.length })}
              </p>
              {crossEnvSelection ? (
                <p className="text-destructive mt-0.5 text-xs">
                  {t("footer.crossEnvWarning")}
                </p>
              ) : (
                <p className="text-muted-foreground mt-0.5 text-xs">
                  {t("footer.summary", { envs: summaryApps })}
                </p>
              )}
            </div>
            {/* #420 Scope C — buttons stack on mobile, full-width, 44px min. */}
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
              <Button
                variant="destructive"
                disabled={bulkDisabled || busy}
                onClick={() => setConfirmReject(true)}
                className="min-h-11 w-full sm:w-auto"
              >
                {bulkRejectState.loading ? (
                  <Loader2Icon className="size-4 animate-spin" />
                ) : (
                  <XCircleIcon className="size-4" />
                )}
                {t("footer.rejectButton", { count: selectedDeploys.length })}
              </Button>
              <Button
                disabled={bulkDisabled || busy}
                onClick={() => setConfirmApprove(true)}
                className="min-h-11 w-full sm:w-auto"
              >
                {bulkApproveState.loading ? (
                  <Loader2Icon className="size-4 animate-spin" />
                ) : (
                  <CheckIcon className="size-4" />
                )}
                {t("footer.approveButton", { count: selectedDeploys.length })}
              </Button>
            </div>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={confirmApprove}
        onOpenChange={setConfirmApprove}
        title={t("confirmApprove.title", { count: selectedDeploys.length })}
        description={t("confirmApprove.description", { envs: summaryApps })}
        confirmLabel={t("confirmApprove.confirm")}
        onConfirm={handleBulkApprove}
      />
      <ConfirmDialogWithReason
        open={confirmReject}
        onOpenChange={setConfirmReject}
        title={t("confirmReject.title", { count: selectedDeploys.length })}
        description={t("confirmReject.description", { envs: summaryApps })}
        reasonLabel={t("confirmReject.reasonLabel")}
        reasonPlaceholder={t("confirmReject.reasonPlaceholder")}
        reasonRequiredError={t("confirmReject.reasonRequired")}
        confirmLabel={t("confirmReject.confirm")}
        destructive
        onConfirm={handleBulkReject}
      />
    </PageShell>
  );
}

function QueueRow({
  deployment,
  checked,
  onToggle,
  canApprove,
  formatRelative,
}: {
  deployment: AstroliftDeployment;
  checked: boolean;
  onToggle: () => void;
  canApprove: boolean;
  formatRelative: (d: Date | string, now?: Date) => string;
}) {
  const t = useTranslations("lists.approvalsQueue.row");
  const shortTag = (deployment.imageTag || deployment.id).slice(0, 10);

  return (
    <div className="bg-background flex flex-col gap-2 rounded-md border p-3 sm:flex-row sm:items-center">
      {canApprove && (
        <label className="inline-flex items-center gap-2 sm:shrink-0">
          <input
            type="checkbox"
            checked={checked}
            onChange={onToggle}
            aria-label={t("selectRowLabel", {
              app: deployment.registeredAppSlug,
              env: deployment.environmentName,
            })}
            className="size-4"
          />
        </label>
      )}
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <GitCommitIcon className="text-muted-foreground size-4 shrink-0" aria-hidden />
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex flex-wrap items-center gap-2">
            <Link
              href={`/approvals/${deployment.id}`}
              className="font-medium hover:underline"
            >
              {deployment.registeredAppSlug} → {deployment.environmentName}
            </Link>
            <span className="font-mono text-xs">{shortTag}</span>
            <Badge variant="outline" className="text-[10px]">
              {t("approvalsCount", {
                received: deployment.approvalsReceived,
                required:
                  deployment.requiredApproverCount || deployment.approvalsRequired || 1,
              })}
            </Badge>
          </div>
          <div className="text-muted-foreground text-xs">
            {t("received", { rel: formatRelative(deployment.createdAt) })}
            {deployment.triggerKind && ` · ${deployment.triggerKind}`}
          </div>
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <Button
          asChild
          size="sm"
          variant="outline"
          className="min-h-11 w-full sm:w-auto"
        >
          <Link href={`/approvals/${deployment.id}`}>{t("review")}</Link>
        </Button>
      </div>
    </div>
  );
}
