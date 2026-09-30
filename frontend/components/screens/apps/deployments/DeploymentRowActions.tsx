"use client";

import { useTranslations } from "next-intl";

import { CheckIcon, RotateCcwIcon, StopCircleIcon, Trash2Icon, UndoIcon } from "lucide-react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import type { AstroliftDeployment } from "@/graphql/lifecycle/lifecycle.types";

import { IN_FLIGHT } from "./app-deployments-format";
import type { useDeploymentActions } from "./use-deployment-actions";

export type DeploymentActions = ReturnType<typeof useDeploymentActions>;

/** The actions that confirm first; approve fires straight from the menu. */
export type ConfirmedAction = "redeploy" | "rollback" | "abort" | "discard";

export interface ActionTarget {
  kind: ConfirmedAction;
  deployment: AstroliftDeployment;
}

/** Which actions a deployment offers the viewer. Pure. */
export function availableActions(
  d: AstroliftDeployment,
  {
    canApprove,
    canDeploy,
    canRollback,
  }: Pick<DeploymentActions, "canApprove" | "canDeploy" | "canRollback">
) {
  return {
    approve:
      d.status === "pending_approval" && d.approvalsReceived < d.approvalsRequired && canApprove,
    redeploy: d.status === "running" && canDeploy,
    rollback:
      (d.status === "running" ||
        d.status === "superseded" ||
        d.status === "rolled_back" ||
        d.status === "failed") &&
      canRollback,
    abort: IN_FLIGHT.has(d.status) && canDeploy,
    discard: d.status === "failed" && canDeploy,
  };
}

export interface DeploymentRowMenuItemsProps {
  deployment: AstroliftDeployment;
  actions: Pick<
    DeploymentActions,
    "canApprove" | "canDeploy" | "canRollback" | "busy" | "onApprove"
  >;
  /** Opens the confirm dialog for an action; the dialog lives outside the menu. */
  onRequest: (target: ActionTarget) => void;
}

/**
 * A deployment row's `⋯` items (spec 44 §5.1): approve, redeploy, roll back,
 * abort, discard. Everything but approve asks first, through
 * `DeploymentActionDialog`, which the screen renders outside the menu so it
 * survives the menu closing.
 */
export function DeploymentRowMenuItems({
  deployment: d,
  actions,
  onRequest,
}: DeploymentRowMenuItemsProps) {
  const t = useTranslations("apps.deployments.actions");
  const show = availableActions(d, actions);
  const { busy } = actions;
  const ask = (kind: ConfirmedAction) => () => onRequest({ kind, deployment: d });

  if (!Object.values(show).some(Boolean)) {
    return <DropdownMenuItem disabled>{t("noActions")}</DropdownMenuItem>;
  }
  return (
    <>
      {show.approve && (
        <DropdownMenuItem disabled={busy} onSelect={() => void actions.onApprove(d)}>
          <CheckIcon className="size-4" />
          {t("approve")}
        </DropdownMenuItem>
      )}
      {show.redeploy && (
        <DropdownMenuItem disabled={busy} onSelect={ask("redeploy")}>
          <RotateCcwIcon className="size-4" />
          {t("redeploy")}
        </DropdownMenuItem>
      )}
      {show.rollback && (
        <DropdownMenuItem disabled={busy} onSelect={ask("rollback")}>
          <UndoIcon className="size-4" />
          {t("rollbackTo")}
        </DropdownMenuItem>
      )}
      {show.abort && (
        <DropdownMenuItem variant="destructive" disabled={busy} onSelect={ask("abort")}>
          <StopCircleIcon className="size-4" />
          {t("abort")}
        </DropdownMenuItem>
      )}
      {show.discard && (
        <DropdownMenuItem variant="destructive" disabled={busy} onSelect={ask("discard")}>
          <Trash2Icon className="size-4" />
          {t("discardFailed")}
        </DropdownMenuItem>
      )}
    </>
  );
}

export interface DeploymentActionDialogProps {
  target: ActionTarget | null;
  appSlug: string;
  onClose: () => void;
  actions: Pick<DeploymentActions, "onAbort" | "onRedeploy" | "onRollback">;
}

/** The one confirm dialog behind the row menus. Copy matches the fleet list. */
export function DeploymentActionDialog({
  target,
  appSlug,
  onClose,
  actions,
}: DeploymentActionDialogProps) {
  const t = useTranslations("apps.deployments.actions");
  const common = useTranslations("apps.common");
  const d = target?.deployment;
  const tag = d ? d.imageTag || d.id.slice(0, 8) : "";
  const onOpenChange = (open: boolean) => {
    if (!open) onClose();
  };

  return (
    <>
      <ConfirmDialog
        reason={{
          label: t("abortReasonLabel"),
          placeholder: t("abortReasonPlaceholder"),
          requiredError: t("requiredReason"),
        }}
        open={target?.kind === "abort"}
        onOpenChange={onOpenChange}
        cancelLabel={common("cancel")}
        title={t("abortTitle", { env: d?.environmentName ?? "" })}
        description={t("abortDescription")}
        confirmLabel={t("abortConfirm")}
        destructive
        onConfirm={(reason) => (d ? actions.onAbort(d, reason) : undefined)}
      />
      {/* Redeploy spawns a real rollout, so it confirms, as on /deployments. */}
      <ConfirmDialog
        open={target?.kind === "redeploy"}
        onOpenChange={onOpenChange}
        cancelLabel={common("cancel")}
        title={t("redeployTitle", { tag, app: appSlug, env: d?.environmentName ?? "" })}
        description={t("redeployDescription")}
        confirmLabel={t("redeploy")}
        onConfirm={() => (d ? actions.onRedeploy(d) : undefined)}
      />
      <ConfirmDialog
        open={target?.kind === "rollback"}
        onOpenChange={onOpenChange}
        cancelLabel={common("cancel")}
        title={t("rollbackTitle", { env: d?.environmentName ?? "", tag })}
        description={t("rollbackDescription")}
        confirmLabel={t("rollbackConfirm")}
        onConfirm={() => (d ? actions.onRollback(d) : undefined)}
      />
      <ConfirmDialog
        reason={{
          label: t("discardReasonLabel"),
          placeholder: t("discardReasonPlaceholder"),
          requiredError: t("requiredReason"),
        }}
        open={target?.kind === "discard"}
        onOpenChange={onOpenChange}
        cancelLabel={common("cancel")}
        title={t("discardTitle", { tag: (d?.imageTag || d?.id || "").slice(0, 8) })}
        description={t("discardDescription")}
        confirmLabel={t("discardConfirm")}
        destructive
        onConfirm={(reason) => (d ? actions.onAbort(d, reason) : undefined)}
      />
    </>
  );
}
