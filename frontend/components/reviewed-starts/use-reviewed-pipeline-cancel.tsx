"use client";
import { useApolloClient } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { REVIEW_PIPELINE_CANCELLATION } from "@/graphql/reviewed-starts/reviewed.queries";
import { CANCEL_REVIEWED_PIPELINE } from "@/graphql/reviewed-starts/reviewed.mutations";

type Review = {
  id: string;
  version: number;
  status: string;
  temporalWorkflowId: string | null;
  temporalRunId: string | null;
  cancellationStatus: string;
  cleanupStatus: string;
};
export function useReviewedPipelineCancel() {
  const client = useApolloClient();
  const t = useTranslations("ReviewedPipelineStart");
  const [id, setId] = useState<string | null>(null);
  const [review, setReview] = useState<Review | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function open(runId: string) {
    setId(runId);
    setConfirmed(false);
    setBusy(true);
    setError(null);
    try {
      const response = await client.query<{ astroliftPipelineRun: Review | null }>({
        query: REVIEW_PIPELINE_CANCELLATION,
        variables: { id: runId },
        fetchPolicy: "no-cache",
      });
      setReview(response.data?.astroliftPipelineRun ?? null);
      if (!response.data?.astroliftPipelineRun) setError(t("unavailable"));
    } catch {
      setReview(null);
      setError(t("unavailable"));
    } finally {
      setBusy(false);
    }
  }
  async function request() {
    if (!review?.temporalWorkflowId || !review.temporalRunId || !confirmed || busy) return;
    setBusy(true);
    setError(null);
    try {
      const response = await client.mutate<{
        cancelPipelineRun: {
          ok: boolean;
          data: Review | null;
          errors: { code: string; message: string }[];
        };
      }>({
        mutation: CANCEL_REVIEWED_PIPELINE,
        variables: {
          runId: review.id,
          expectedVersion: review.version,
          temporalWorkflowId: review.temporalWorkflowId,
          temporalRunId: review.temporalRunId,
        },
      });
      const value = response.data?.cancelPipelineRun;
      if (value?.data) setReview(value.data);
      if (!value?.ok)
        setError(
          value?.errors.map((e) => `${e.code}: ${e.message}`).join(" · ") || t("cancelUncertain")
        );
      setConfirmed(false);
    } catch {
      setError(t("cancelUncertain"));
      setConfirmed(false);
    } finally {
      setBusy(false);
    }
  }
  const dialog = (
    <Sheet
      open={id !== null}
      onOpenChange={(value) => {
        if (!value && !busy) setId(null);
      }}
    >
      <SheetContent showCloseButton={false}>
        <SheetHeader>
          <SheetTitle>{t("cancelTitle")}</SheetTitle>
          <SheetDescription>{t("cancelDescription")}</SheetDescription>
        </SheetHeader>
        <div className="space-y-4 overflow-auto px-4">
          {busy && <p role="status">{t("loading")}</p>}
          {error && (
            <p role="alert" className="text-destructive">
              {error}
            </p>
          )}
          {review && (
            <>
              <dl className="space-y-2 text-xs break-all">
                <div>
                  <dt>{t("execution")}</dt>
                  <dd>{review.id}</dd>
                </div>
                <div>
                  <dt>{t("revision")}</dt>
                  <dd>{review.version}</dd>
                </div>
                <div>
                  <dt>{t("engine")}</dt>
                  <dd>{review.temporalWorkflowId}</dd>
                </div>
                <div>
                  <dt>{t("engineRun")}</dt>
                  <dd>{review.temporalRunId ?? t("pending")}</dd>
                </div>
                <div>
                  <dt>{t("cancellationStatus")}</dt>
                  <dd>{review.cancellationStatus}</dd>
                </div>
                <div>
                  <dt>{t("cleanupStatus")}</dt>
                  <dd>{review.cleanupStatus}</dd>
                </div>
              </dl>
              {review.cancellationStatus === "acknowledged" && (
                <p role="status">{t("cancelAcknowledged")}</p>
              )}
              {review.cancellationStatus === "uncertain" && (
                <p role="status">{t("cancelUncertain")}</p>
              )}
              {review.cancellationStatus === "observed" && (
                <p role="status">{t("cancelObserved")}</p>
              )}
              {review.cleanupStatus === "pending" && <p>{t("cleanupPending")}</p>}
              {review.cleanupStatus === "failed" && <p role="alert">{t("cleanupFailed")}</p>}
              <Label className="flex items-start gap-2">
                <input
                  type="checkbox"
                  checked={confirmed}
                  onChange={(e) => setConfirmed(e.target.checked)}
                  disabled={
                    busy || !review.temporalRunId || !["pending", "running"].includes(review.status)
                  }
                />
                {t("cancelConfirm")}
              </Label>
            </>
          )}
        </div>
        <SheetFooter>
          <Button variant="outline" disabled={busy} onClick={() => setId(null)}>
            {t("close")}
          </Button>
          <Button variant="outline" disabled={busy || !id} onClick={() => id && void open(id)}>
            {t("reconcile")}
          </Button>
          <Button
            disabled={
              busy ||
              !confirmed ||
              !review?.temporalRunId ||
              ["acknowledged", "observed"].includes(review.cancellationStatus)
            }
            onClick={() => void request()}
          >
            {t("cancelRequest")}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
  return { open, dialog };
}
