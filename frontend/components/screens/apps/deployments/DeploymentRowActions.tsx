"use client";

import { CheckIcon, RotateCcwIcon, StopCircleIcon, Trash2Icon, UndoIcon } from "lucide-react";
import * as React from "react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import type { AstroliftDeployment } from "@/graphql/lifecycle/lifecycle.types";

import { IN_FLIGHT } from "./app-deployments-format";
import type { useDeploymentActions } from "./use-deployment-actions";

export type DeploymentRowActionsViewProps = ReturnType<typeof useDeploymentActions> & {
  deployment: AstroliftDeployment;
  appSlug: string;
};

/** Approve / redeploy / rollback / abort / discard for one deployment row. */
export function DeploymentRowActionsView({
  deployment,
  appSlug,
  canApprove,
  canDeploy,
  canRollback,
  busy,
  onApprove,
  onAbort,
  onRedeploy,
  onRollback,
}: DeploymentRowActionsViewProps) {
  const d = deployment;

  const [confirmAbort, setConfirmAbort] = React.useState(false);
  const [confirmRedeploy, setConfirmRedeploy] = React.useState(false);
  const [confirmRollback, setConfirmRollback] = React.useState(false);
  const [confirmTrash, setConfirmTrash] = React.useState(false);

  const showApprove =
    d.status === "pending_approval" && d.approvalsReceived < d.approvalsRequired && canApprove;
  const inFlight = IN_FLIGHT.has(d.status);
  const showAbort = inFlight && canDeploy;
  const showRedeploy = d.status === "running" && canDeploy;
  const showRollback =
    (d.status === "running" || d.status === "superseded" || d.status === "rolled_back") &&
    canRollback;
  const showFailedRollback = d.status === "failed" && canRollback;
  const showFailedTrash = d.status === "failed" && canDeploy;

  return (
    <div className="inline-flex items-center justify-end gap-1">
      {showApprove && (
        <Button size="sm" variant="default" disabled={busy} onClick={onApprove}>
          <CheckIcon className="size-3.5" />
          Approve
        </Button>
      )}
      {showRedeploy && (
        <Button
          size="sm"
          variant="outline"
          disabled={busy}
          onClick={() => setConfirmRedeploy(true)}
        >
          <RotateCcwIcon className="size-3.5" />
          Redeploy
        </Button>
      )}
      {(showRollback || showFailedRollback) && (
        <Button
          size="sm"
          variant="outline"
          disabled={busy}
          onClick={() => setConfirmRollback(true)}
        >
          <UndoIcon className="size-3.5" />
          Rollback
        </Button>
      )}
      {showAbort && (
        <Button
          size="sm"
          variant="destructive"
          disabled={busy}
          onClick={() => setConfirmAbort(true)}
        >
          <StopCircleIcon className="size-3.5" />
          Abort
        </Button>
      )}
      {showFailedTrash && (
        <Button
          size="sm"
          variant="ghost"
          disabled={busy}
          aria-label="Discard failed deployment"
          onClick={() => setConfirmTrash(true)}
        >
          <Trash2Icon className="size-3.5" />
        </Button>
      )}

      <ConfirmDialog
        reason={{ label: "Reason for abort", placeholder: "Why are you aborting this deploy?" }}
        open={confirmAbort}
        onOpenChange={setConfirmAbort}
        title={`Abort deploy to ${d.environmentName}?`}
        description="The in-flight rollout will be marked failed. Tell the team what changed."
        confirmLabel="Abort deploy"
        destructive
        onConfirm={onAbort}
      />

      {/* Redeploy spawns a real rollout — the same mutation /deployments
          confirms before firing, and the sibling actions in this very row
          already do. Wording matches the fleet surface so the two pages
          describe the same action the same way. */}
      <ConfirmDialog
        open={confirmRedeploy}
        onOpenChange={setConfirmRedeploy}
        title={`Redeploy ${d.imageTag || d.id.slice(0, 8)} to ${appSlug}/${d.environmentName}?`}
        description="Spawns a fresh deployment with the same image. Useful to retry after a transient failure or pick up an updated config."
        confirmLabel="Redeploy"
        onConfirm={onRedeploy}
      />

      <ConfirmDialog
        open={confirmRollback}
        onOpenChange={setConfirmRollback}
        title={`Rollback ${d.environmentName} to ${d.imageTag || d.id.slice(0, 8)}?`}
        description="The platform will redeploy this image as the live version. The current rollout will be marked superseded."
        confirmLabel="Roll back"
        onConfirm={onRollback}
      />

      <ConfirmDialog
        reason={{ label: "Reason for discard", placeholder: "Why are you discarding this deploy?" }}
        open={confirmTrash}
        onOpenChange={setConfirmTrash}
        title={`Discard failed deploy ${(d.imageTag || d.id).slice(0, 8)}?`}
        description="The row stays in history but the rollout is marked aborted. Tell the team what changed."
        confirmLabel="Discard"
        destructive
        onConfirm={onAbort}
      />
    </div>
  );
}
