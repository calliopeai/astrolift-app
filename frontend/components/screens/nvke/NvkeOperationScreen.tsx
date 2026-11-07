"use client";

import { DownloadIcon, RefreshCwIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DefinitionList } from "@/components/ui/definition-list";
import { Skeleton } from "@/components/ui/skeleton";

import { nvkeFinished, type NvkeOperationScreenProps } from "./types";

export function NvkeOperationScreen({
  operation,
  siteName,
  loading,
  error,
  pending,
  canAct,
  canResume,
  onRefresh,
  onCancel,
  onResume,
  onDownload,
}: NvkeOperationScreenProps) {
  const t = useTranslations("nvke");
  const [confirm, setConfirm] = useState<"cancel" | "resume" | "download" | null>(null);
  if (loading && !operation)
    return (
      <PageShell title={t("operations")}>
        <Skeleton className="h-64" />
      </PageShell>
    );
  if (!operation)
    return (
      <PageShell title={t("operations")}>
        <p role="alert">{error ?? t("operationUnavailable")}</p>
        <Button variant="outline" onClick={onRefresh}>
          {t("refresh")}
        </Button>
      </PageShell>
    );
  const allowed = canAct && !pending && !error;
  return (
    <PageShell
      title={operation.operationKey}
      description={
        <Link className="hover:underline" href={`/nvke/${operation.siteId}`}>
          {siteName || t("title")}
        </Link>
      }
      actions={
        <>
          <Button variant="outline" disabled={pending} onClick={onRefresh}>
            <RefreshCwIcon className="size-4" />
            {t("refresh")}
          </Button>
          <Button variant="outline" disabled={!allowed} onClick={() => setConfirm("download")}>
            <DownloadIcon className="size-4" />
            {t("downloadResult")}
          </Button>
          {!nvkeFinished(operation.status) && (
            <Button
              variant="outline"
              disabled={!allowed || Boolean(operation.cancellationRequestedAt)}
              onClick={() => setConfirm("cancel")}
            >
              {t("cancelOperation")}
            </Button>
          )}
          {operation.status === "UNCERTAIN" && (
            <Button disabled={!canResume || !allowed} onClick={() => setConfirm("resume")}>
              {t("reconcile")}
            </Button>
          )}
        </>
      }
    >
      {error && (
        <p role="alert" className="text-danger-fg text-sm">
          {error}
        </p>
      )}
      <div
        role="status"
        className={`rounded-lg p-4 text-sm ${operation.status === "SUCCEEDED" ? "bg-success-bg text-success-fg" : operation.status === "FAILED" || operation.status === "UNCERTAIN" ? "bg-warning-bg text-warning-fg" : "bg-info-bg text-info-fg"}`}
      >
        <Badge variant="outline">{t(`status.${operation.status}`)}</Badge>
        <p className="mt-2">
          {t(
            operation.status === "SUCCEEDED"
              ? "observedHelp"
              : operation.status === "UNCERTAIN"
                ? "uncertainHelp"
                : operation.status === "CANCELLED"
                  ? "cancelledHelp"
                  : operation.status === "FAILED"
                    ? "failedHelp"
                    : "acceptedHelp"
          )}
        </p>
      </div>
      <DefinitionList
        items={[
          {
            term: t("operationId"),
            description: <code className="text-xs [overflow-wrap:anywhere]">{operation.id}</code>,
          },
          {
            term: t("requestId"),
            description: (
              <code className="text-xs [overflow-wrap:anywhere]">{operation.requestId}</code>
            ),
          },
          {
            term: t("stage"),
            description: (
              <code className="text-xs [overflow-wrap:anywhere]">{operation.stage || "—"}</code>
            ),
          },
          { term: t("version"), description: operation.version },
          { term: t("generation"), description: operation.generation },
          {
            term: t("deadline"),
            description: <time dateTime={operation.deadline}>{operation.deadline}</time>,
          },
          { term: t("errorCode"), description: <code>{operation.errorCode || "—"}</code> },
          {
            term: t("cancellation"),
            description: operation.cancellationRequestedAt ? t("cancelRequested") : t("none"),
          },
        ]}
      />
      <p className="text-muted-foreground text-sm">{t("resultHelp")}</p>
      <ConfirmDialog
        open={Boolean(confirm)}
        onOpenChange={(open) => {
          if (!open) setConfirm(null);
        }}
        title={t(
          confirm === "resume"
            ? "reconcile"
            : confirm === "download"
              ? "downloadResult"
              : "cancelOperation"
        )}
        description={t(
          confirm === "resume"
            ? "reconcileHelp"
            : confirm === "download"
              ? "resultHelp"
              : "cancelHelp"
        )}
        destructive={confirm === "cancel"}
        confirmDisabled={!allowed || (confirm === "resume" && !canResume)}
        onConfirm={async () => {
          if (confirm === "resume") return onResume();
          if (confirm === "download") {
            await onDownload();
            return true;
          }
          return onCancel();
        }}
      />
    </PageShell>
  );
}
