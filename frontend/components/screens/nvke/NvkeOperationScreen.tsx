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
import { nativeMaintenancePermission } from "@/lib/nvke/contract";
import { nvkeInventoryHref } from "@/lib/nvke/inventory";

import { NvkeDiagnosticsPanel } from "./NvkeDiagnosticsPanel";
import { NvkeWorkflowPanel } from "./NvkeWorkflowPanel";
import { NvkeMaintenancePanel } from "./NvkeMaintenancePanel";
import { NvkeProvidersPanel } from "./NvkeProvidersPanel";
import { NvkeCredentialPanel } from "./NvkeCredentialPanel";
import { NvkeCredentialRequestPanel } from "./NvkeCredentialRequestPanel";
import { NvkeWorkloadAccessPanel } from "./NvkeWorkloadAccessPanel";
import { NvkeOperatorInstallPlanContextPanel } from "./NvkeOperatorInstallPlanContextPanel";
import { NvkeOperatorInstallPlanLocalImagePanel } from "./NvkeOperatorInstallPlanLocalImagePanel";
import { NvkeOperatorInstallPlanImageBindingPanel } from "./NvkeOperatorInstallPlanImageBindingPanel";
import { NvkeOperatorInstallPlanWorkloadPanel } from "./NvkeOperatorInstallPlanWorkloadPanel";
import { NvkeOperatorInstallPlanImagePanel } from "./NvkeOperatorInstallPlanImagePanel";
import { NvkeOperatorInstallPlanVerificationPanel } from "./NvkeOperatorInstallPlanVerificationPanel";
import { NvkeOperatorInstallPlanTreePanel } from "./NvkeOperatorInstallPlanTreePanel";
import { NvkeOperatorInstallPlanChartsPanel } from "./NvkeOperatorInstallPlanChartsPanel";
import { NvkeOperatorInstallPlanPanel } from "./NvkeOperatorInstallPlanPanel";
import { NvkeOperatorInstallReviewPanel } from "./NvkeOperatorInstallReviewPanel";
import { NvkeOperatorJobPanel } from "./NvkeOperatorJobPanel";
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
  diagnostics,
  maintenance,
  credentials,
  providers,
  credentialRequest,
  workflows,
  workloadAccess,
  operatorJob,
  operatorInstallReview,
  operatorInstallPlan,
  operatorInstallPlanCharts,
  operatorInstallPlanTree,
  operatorInstallPlanContext,
  operatorInstallPlanVerification,
  operatorInstallPlanImage,
  operatorInstallPlanWorkloadImage,
  operatorInstallPlanWorkload,
  operatorInstallPlanImageBinding,
  operatorInstallPlanLocalImage,
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
  const native = !!nativeMaintenancePermission(operation.operationKey);
  const tooling = operation.operationKey.startsWith("tooling.");
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
          {operation.isInventory && operation.status === "SUCCEEDED" && (
            <Button variant="outline" asChild>
              <Link
                href={nvkeInventoryHref(operation.siteId, operation.operationKey, operation.id)}
              >
                {t("resourcesTitle")}
              </Link>
            </Button>
          )}
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
          {operation.status === "UNCERTAIN" && !tooling && (
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
          {tooling
            ? t("operatorJob.processHelp")
            : operation.operationKey === "infrastructure.providers" &&
                operation.status === "SUCCEEDED"
              ? t("providers.collectedHelp")
              : native
                ? t("workflow.reviewHelp")
                : t(
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
      {operatorJob && (
        <NvkeOperatorJobPanel
          {...operatorJob}
          canManage={operatorJob.canManage && canAct && !error && (!pending || operatorJob.pending)}
        />
      )}
      {operatorInstallReview && (
        <NvkeOperatorInstallReviewPanel
          {...operatorInstallReview}
          canReview={operatorInstallReview.canReview && canAct && !error}
          pending={pending || operatorInstallReview.pending}
        />
      )}
      {operatorInstallPlan && (
        <NvkeOperatorInstallPlanPanel
          {...operatorInstallPlan}
          canReview={operatorInstallPlan.canReview && canAct && !error}
          pending={pending || operatorInstallPlan.pending}
        />
      )}
      {operatorInstallPlanCharts && (
        <NvkeOperatorInstallPlanChartsPanel
          {...operatorInstallPlanCharts}
          canReview={operatorInstallPlanCharts.canReview && canAct && !error}
          pending={pending || operatorInstallPlanCharts.pending}
        />
      )}
      {operatorInstallPlanTree && (
        <NvkeOperatorInstallPlanTreePanel
          {...operatorInstallPlanTree}
          canReview={operatorInstallPlanTree.canReview && canAct && !error}
          pending={pending || operatorInstallPlanTree.pending}
        />
      )}
      {operatorInstallPlanContext && (
        <NvkeOperatorInstallPlanContextPanel
          {...operatorInstallPlanContext}
          canReview={operatorInstallPlanContext.canReview && canAct && !error}
          pending={pending || operatorInstallPlanContext.pending}
        />
      )}
      {operatorInstallPlanVerification && (
        <NvkeOperatorInstallPlanVerificationPanel
          {...operatorInstallPlanVerification}
          canReview={operatorInstallPlanVerification.canReview && canAct && !error}
          pending={pending || operatorInstallPlanVerification.pending}
        />
      )}
      {operatorInstallPlanWorkload && (
        <NvkeOperatorInstallPlanWorkloadPanel
          {...operatorInstallPlanWorkload}
          canReview={operatorInstallPlanWorkload.canReview && canAct && !error}
          pending={pending || operatorInstallPlanWorkload.pending}
        />
      )}
      {operatorInstallPlanWorkloadImage && (
        <NvkeOperatorInstallPlanImagePanel
          {...operatorInstallPlanWorkloadImage}
          canReview={operatorInstallPlanWorkloadImage.canReview && canAct && !error}
          pending={pending || operatorInstallPlanWorkloadImage.pending}
        />
      )}
      {operatorInstallPlanImageBinding && (
        <NvkeOperatorInstallPlanImageBindingPanel
          {...operatorInstallPlanImageBinding}
          canReview={operatorInstallPlanImageBinding.canReview && canAct && !error}
          pending={pending || operatorInstallPlanImageBinding.pending}
        />
      )}
      {operatorInstallPlanImage && (
        <NvkeOperatorInstallPlanImagePanel
          {...operatorInstallPlanImage}
          canReview={operatorInstallPlanImage.canReview && canAct && !error}
          pending={pending || operatorInstallPlanImage.pending}
        />
      )}
      {operatorInstallPlanLocalImage && (
        <NvkeOperatorInstallPlanLocalImagePanel
          {...operatorInstallPlanLocalImage}
          canReview={operatorInstallPlanLocalImage.canReview && canAct && !error}
          pending={pending || operatorInstallPlanLocalImage.pending}
        />
      )}
      {diagnostics && <NvkeDiagnosticsPanel {...diagnostics} />}
      {maintenance && <NvkeMaintenancePanel {...maintenance} />}
      {credentials && <NvkeCredentialPanel {...credentials} />}
      {providers && <NvkeProvidersPanel {...providers} />}
      {credentialRequest && <NvkeCredentialRequestPanel {...credentialRequest} />}
      {workflows && <NvkeWorkflowPanel {...workflows} />}
      {workloadAccess && <NvkeWorkloadAccessPanel {...workloadAccess} />}
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
        description={
          tooling && confirm === "cancel"
            ? t("operatorJob.cancelHelp")
            : native && confirm !== "download"
              ? t(confirm === "resume" ? "workflow.resumeHelp" : "workflow.cancelHelp")
              : t(
                  confirm === "resume"
                    ? "reconcileHelp"
                    : confirm === "download"
                      ? "resultHelp"
                      : "cancelHelp"
                )
        }
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
