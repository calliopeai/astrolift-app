"use client";

import { useTranslations } from "next-intl";
import { toast } from "sonner";

export type DeploymentFeedbackAction = "approve" | "abort" | "redeploy" | "rollbackConfirm";
const STATUSES = new Set([
  "pending_approval",
  "pending",
  "deploying",
  "redeploying",
  "running",
  "failed",
  "superseded",
  "rolled_back",
]);

/** Product labels cover known states; future server tokens remain literal. */
export function useDeploymentFeedback() {
  const t = useTranslations("apps.deployments.actions");
  const statusLabel = useTranslations("apps.deployments.statuses");
  function status(status: string | null | undefined) {
    return status ? (STATUSES.has(status) ? statusLabel(status) : status) : t("defaultStatus");
  }
  function failed(action: DeploymentFeedbackAction) {
    return t("failed", { action: t(action) });
  }
  function report(
    action: DeploymentFeedbackAction,
    result:
      | { ok: boolean; errors: { message: string }[]; data?: { status: string } | null }
      | null
      | undefined,
    ignoreMissing = false
  ) {
    if (!result) {
      if (ignoreMissing) return;
      throw new Error(failed(action));
    }
    if (!result.ok) throw new Error(result.errors[0]?.message ?? failed(action));
    toast.success(t("success", { action: t(action), status: status(result.data?.status) }));
  }
  return { report, failed, status };
}
