"use client";
import { useLayoutEffect, useRef, useState } from "react";
import Link from "next/link";
import { useFormatter, useTranslations } from "next-intl";
import { PageShell } from "@/components/PageShell";
import { QueryError } from "@/components/QueryError";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { DefinitionList } from "@/components/ui/definition-list";
import type { ModelConnectionRequestFieldsFragment } from "@/graphql/__generated__/operations";
import { connectionStatus } from "./ModelConnectionRequestsScreen";
export type ConnectionDecision = "approve" | "reject" | "cancel" | "finalize";
export type ConnectionDecisionReview = {
  action: ConnectionDecision;
  request: ModelConnectionRequestFieldsFragment;
};
export type ConnectionDecisionResult =
  | { accepted: true; row: ModelConnectionRequestFieldsFragment | null }
  | { accepted: false; message: string };
export type ModelConnectionRequestProps = {
  scopeKey: string;
  row: ModelConnectionRequestFieldsFragment | null;
  loading: boolean;
  stale: boolean;
  error: string | null;
  supported: boolean | null;
  supportError: string | null;
  onRetry: () => void;
  onRetrySupport: () => void;
  onSubmit: (review: ConnectionDecisionReview) => Promise<ConnectionDecisionResult>;
  onAccepted: (row: ModelConnectionRequestFieldsFragment | null) => Promise<unknown>;
};
export function ModelConnectionRequestScreen(props: ModelConnectionRequestProps) {
  return <Request key={props.scopeKey} {...props} />;
}
function Request(props: ModelConnectionRequestProps) {
  const t = useTranslations("models.shared.connections"),
    format = useFormatter();
  const [review, setReview] = useState<{
      input: ConnectionDecisionReview;
      revision: number;
    } | null>(null),
    [failure, setFailure] = useState<string | null>(null),
    [receipt, setReceipt] = useState<{
      row: ModelConnectionRequestFieldsFragment | null;
      refreshFailed: boolean;
    } | null>(null);
  const snapshot = JSON.stringify([
    props.row,
    props.loading,
    props.stale,
    props.error,
    props.supported,
    props.supportError,
  ]);
  const [binding, setBinding] = useState({ key: snapshot, revision: 0 });
  if (binding.key !== snapshot) setBinding({ key: snapshot, revision: binding.revision + 1 });
  const latest = useRef({ props, revision: binding.revision }),
    mounted = useRef(false);
  useLayoutEffect(() => {
    latest.current = { props, revision: binding.revision };
  });
  useLayoutEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const ready =
    props.supported === true &&
    !props.supportError &&
    !props.loading &&
    !props.stale &&
    !props.error &&
    !!props.row;
  const allowed = (action: ConnectionDecision, row: ModelConnectionRequestFieldsFragment) =>
    action === "approve"
      ? row.canApprove === true
      : action === "reject"
        ? row.canReject === true
        : action === "cancel"
          ? row.canCancel === true
          : row.canFinalize === true;
  const current =
    !!review &&
    ready &&
    review.revision === binding.revision &&
    !!props.row &&
    allowed(review.input.action, props.row);
  async function confirm() {
    const candidate = review;
    if (!candidate || !mounted.current || candidate.revision !== latest.current.revision)
      return false;
    setFailure(null);
    let result: ConnectionDecisionResult;
    try {
      result = await latest.current.props.onSubmit(candidate.input);
    } catch {
      if (mounted.current && candidate.revision === latest.current.revision)
        setFailure(t("uncertain"));
      return false;
    }
    if (!mounted.current || candidate.revision !== latest.current.revision) return false;
    if (!result.accepted) {
      setFailure(result.message);
      return false;
    }
    setReceipt({ row: result.row, refreshFailed: false });
    setReview(null);
    try {
      await latest.current.props.onAccepted(result.row);
    } catch {
      if (mounted.current) setReceipt({ row: result.row, refreshFailed: true });
    }
    return true;
  }
  const row = props.row;
  return (
    <PageShell
      title={t("openRequest")}
      actions={
        <Button variant="outline" asChild>
          <Link href="/models/connections">{t("backToRequests")}</Link>
        </Button>
      }
    >
      <div className="space-y-4">
        {props.supported !== true ? (
          <>
            <p role={props.supportError ? "alert" : "status"}>
              {props.supportError ??
                t(props.supported === false ? "unsupported" : "capabilityChecking")}
            </p>
            <Button onClick={props.onRetrySupport}>{t("retry")}</Button>
          </>
        ) : (
          <>
            {props.error && (
              <QueryError
                title={t("requestFailed")}
                error={props.error}
                onRetry={props.onRetry}
                retryLabel={t("retry")}
              />
            )}
            {props.loading && <p role="status">{t("capabilityChecking")}</p>}
            {!row && !props.loading && !props.error && <p>{t("notFound")}</p>}
            {row && (
              <>
                <DefinitionList
                  items={[
                    { term: t("requestId"), description: row.id },
                    { term: t("version"), description: format.number(row.version) },
                    { term: t("model"), description: row.modelName ?? row.modelDeploymentId },
                    {
                      term: t("destination"),
                      description: `${row.appName ?? row.appId} / ${row.environmentName ?? row.appEnvironmentId}`,
                    },
                    { term: t("alias"), description: row.alias },
                    { term: t("requester"), description: row.requesterUsername ?? t("unknown") },
                    {
                      term: t("status"),
                      description: connectionStatus(row.status)
                        ? t(connectionStatus(row.status)!)
                        : row.status,
                    },
                    {
                      term: t("approval"),
                      description: t("votes", {
                        count: row.approvalCount,
                        required: row.requiredApprovals,
                      }),
                    },
                  ]}
                />
                {row.subscriptionId ? (
                  <p role="status">{t("connectionRecorded")}</p>
                ) : row.status === "APPROVED" ? (
                  <p>{t("approvedNotice")}</p>
                ) : row.status === "PENDING" ? (
                  <p>{t("requestNotice")}</p>
                ) : null}
                <div className="flex flex-wrap gap-2">
                  {(["approve", "reject", "cancel", "finalize"] as const)
                    .filter((action) => allowed(action, row))
                    .map((action) => (
                      <Button
                        key={action}
                        variant={action === "finalize" ? "default" : "outline"}
                        disabled={!ready}
                        onClick={() => {
                          setFailure(null);
                          setReceipt(null);
                          setReview({
                            input: { action, request: row },
                            revision: binding.revision,
                          });
                        }}
                      >
                        {t(
                          action === "cancel"
                            ? "cancelRequest"
                            : action === "finalize"
                              ? "connect"
                              : action
                        )}
                      </Button>
                    ))}
                </div>
              </>
            )}
          </>
        )}
        {receipt && (
          <div role="status">
            <p>
              {t(
                !receipt.row
                  ? "acceptedUnverified"
                  : receipt.row.subscriptionId
                    ? "connectionRecorded"
                    : "decisionSaved"
              )}
            </p>
            {receipt.refreshFailed && <p>{t("refreshFailed")}</p>}
          </div>
        )}
        {failure && <p role="alert">{failure}</p>}
        <ConfirmDialog
          key={review?.revision ?? "closed"}
          open={!!review}
          confirmDisabled={!current}
          title={t(
            review?.input.action === "approve"
              ? "reviewApprove"
              : review?.input.action === "reject"
                ? "reviewReject"
                : review?.input.action === "cancel"
                  ? "reviewCancel"
                  : "reviewAuto"
          )}
          description={
            <div className="space-y-2">
              <p>
                {review &&
                  t("reviewTarget", {
                    model: review.input.request.modelName ?? review.input.request.modelDeploymentId,
                    target: `${review.input.request.appName ?? review.input.request.appId} / ${review.input.request.environmentName ?? review.input.request.appEnvironmentId}`,
                    alias: review.input.request.alias,
                  })}
              </p>
              <p>{t(review?.input.action === "finalize" ? "finalizeNotice" : "decisionNotice")}</p>
              {!current && <p>{t("changed")}</p>}
            </div>
          }
          confirmLabel={t(
            review?.input.action === "cancel"
              ? "cancelRequest"
              : review?.input.action === "finalize"
                ? "connect"
                : (review?.input.action ?? "review")
          )}
          onConfirm={confirm}
          onOpenChange={(open) => {
            if (!open) setReview((existing) => (existing === review ? null : existing));
          }}
        />
      </div>
    </PageShell>
  );
}
