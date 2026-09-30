"use client";

import {
  EyeIcon,
  EyeOffIcon,
  KeyRoundIcon,
  LinkIcon,
  Loader2Icon,
  Trash2Icon,
  UnlinkIcon,
} from "lucide-react";
import * as React from "react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { AstroliftAgentSecretStatus } from "@/graphql/agents/agents.types";

import type { useAgentSecrets } from "./use-agent-secrets";

export type AgentSecretsViewProps = ReturnType<typeof useAgentSecrets> & {
  envSpecName: string;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** Render inline as cards (the agent Secrets route) instead of a dialog. */
  embedded?: boolean;
  /** Spec-level settings above the refs (model source, live VNC session). */
  runtimeSettings?: React.ReactNode;
  /** The reusable secret bundles card. */
  bundles?: React.ReactNode;
};

/**
 * Manage the VALUES behind an env spec's secret refs (#1173). Lists each ref
 * with a Set / Missing / Error chip (from `agentEnvironmentSpecSecretStatus`)
 * and offers set/rotate (password input, cleared after submit), an explicit
 * audited Reveal action when the provider supports reads, and confirmed delete.
 */
export function AgentSecretsView({
  rows,
  loading,
  refNamespace,
  reveals,
  busyVar,
  pending,
  clearReveals,
  onSave,
  onDeleteValue,
  onUpsertRef,
  onRemoveRef,
  onReveal,
  envSpecName,
  open,
  onOpenChange,
  embedded = false,
  runtimeSettings,
  bundles,
}: AgentSecretsViewProps) {
  // Per-ref draft value (write-only; never seeded from the server).
  const [drafts, setDrafts] = React.useState<Record<string, string>>({});
  const [refEnvVar, setRefEnvVar] = React.useState("");
  const [refUri, setRefUri] = React.useState("");
  const [deleteTarget, setDeleteTarget] = React.useState<AstroliftAgentSecretStatus | null>(null);
  const [removeRefTarget, setRemoveRefTarget] = React.useState<AstroliftAgentSecretStatus | null>(
    null
  );

  // Drop any typed-but-unsaved values as the dialog closes so a plaintext
  // value never lingers in state between opens. Done in the close handler
  // (not an effect) to avoid a cascading render.
  function handleOpenChange(next: boolean) {
    if (!next) {
      setDrafts({});
      clearReveals();
    }
    onOpenChange?.(next);
  }

  async function handleSave(envVar: string) {
    // Clear the write draft immediately; Reveal is a separate audited action.
    if (await onSave(envVar, drafts[envVar] ?? "")) setDrafts((d) => ({ ...d, [envVar]: "" }));
  }

  async function onConfirmDelete() {
    const target = deleteTarget;
    if (!target) return;
    try {
      await onDeleteValue(target);
    } finally {
      setDeleteTarget(null);
    }
  }

  async function handleUpsertRef() {
    const submittedEnvVar = refEnvVar;
    const submittedUri = refUri;
    if (await onUpsertRef(submittedEnvVar, submittedUri)) {
      setRefEnvVar((current) => (current === submittedEnvVar ? "" : current));
      setRefUri((current) => (current === submittedUri ? "" : current));
    }
  }

  function statusBadge(r: AstroliftAgentSecretStatus) {
    if (r.error) {
      return (
        <Badge variant="destructive" title={r.error}>
          Error
        </Badge>
      );
    }
    return r.exists ? <Badge>Set</Badge> : <Badge variant="outline">Missing</Badge>;
  }

  const values = (
    <>
      {runtimeSettings}

      <div className="grid gap-2 rounded-md border p-3 md:grid-cols-[1fr_1.5fr_auto]">
        <Input
          className="font-mono"
          placeholder="ENV_VAR"
          value={refEnvVar}
          onChange={(event) => setRefEnvVar(event.target.value)}
        />
        <Input
          className="font-mono"
          placeholder={`Provider URI (for example ${refNamespace}/token)`}
          value={refUri}
          onChange={(event) => setRefUri(event.target.value)}
        />
        <Button
          onClick={handleUpsertRef}
          disabled={
            !refEnvVar.trim() ||
            !refUri.trim() ||
            pending.has(`ref:${refEnvVar.trim()}`) ||
            busyVar === `ref:${refEnvVar.trim()}`
          }
        >
          {pending.has(`ref:${refEnvVar.trim()}`) || busyVar === `ref:${refEnvVar.trim()}` ? (
            <Loader2Icon className="size-4 animate-spin" />
          ) : (
            <LinkIcon className="size-4" />
          )}{" "}
          Add / update ref
        </Button>
      </div>

      {loading && rows.length === 0 ? (
        <div className="text-muted-foreground flex items-center gap-2 py-6 text-sm">
          <Loader2Icon className="size-4 animate-spin" />
          Checking secret status…
        </div>
      ) : rows.length === 0 ? (
        <p className="text-muted-foreground py-6 text-sm">
          This agent has no direct secret refs. Add one above or attach a reusable bundle below.
        </p>
      ) : (
        <div className="space-y-4">
          {rows.map((r) => (
            <div key={r.envVar} className="space-y-2 rounded-md border p-3">
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate font-mono text-sm font-medium">{r.envVar}</p>
                  <p className="text-muted-foreground truncate font-mono text-xs">{r.uri}</p>
                  <p className="text-muted-foreground text-xs">
                    {r.provider}
                    {!r.canReveal && r.readLimitation ? ` · ${r.readLimitation}` : ""}
                  </p>
                </div>
                {statusBadge(r)}
              </div>
              {reveals[r.envVar] && (
                <code className="bg-muted block overflow-x-auto rounded px-2 py-1.5 text-xs">
                  {reveals[r.envVar]}
                </code>
              )}
              <div className="flex items-center gap-2">
                <Input
                  type="password"
                  autoComplete="new-password"
                  placeholder={r.exists ? "Enter a new value to rotate" : "Enter value to set"}
                  value={drafts[r.envVar] ?? ""}
                  onChange={(e) => setDrafts((d) => ({ ...d, [r.envVar]: e.target.value }))}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") handleSave(r.envVar);
                  }}
                  className="font-mono"
                />
                <Button
                  type="button"
                  size="sm"
                  onClick={() => handleSave(r.envVar)}
                  disabled={
                    pending.has(r.envVar) || busyVar === r.envVar || !(drafts[r.envVar] ?? "")
                  }
                >
                  {(pending.has(r.envVar) || busyVar === r.envVar) && (
                    <Loader2Icon className="size-4 animate-spin" />
                  )}
                  {r.exists ? "Rotate" : "Set"}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  aria-label={`${reveals[r.envVar] ? "Hide" : "Reveal"} ${r.envVar}`}
                  onClick={() => onReveal(r)}
                  disabled={
                    !r.exists ||
                    !r.canReveal ||
                    pending.has(`reveal:${r.envVar}`) ||
                    busyVar === `reveal:${r.envVar}`
                  }
                >
                  {reveals[r.envVar] ? (
                    <EyeOffIcon className="size-4" />
                  ) : (
                    <EyeIcon className="size-4" />
                  )}
                  {reveals[r.envVar] ? "Hide" : "Reveal"}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  aria-label={`Delete ${r.envVar}`}
                  onClick={() => setDeleteTarget(r)}
                  disabled={pending.has(r.envVar) || busyVar === r.envVar || !r.exists}
                >
                  <Trash2Icon className="size-4" />
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  aria-label={`Remove binding ${r.envVar}`}
                  onClick={() => setRemoveRefTarget(r)}
                  disabled={pending.has(`remove:${r.envVar}`) || busyVar === `remove:${r.envVar}`}
                >
                  <UnlinkIcon className="size-4" />
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  );

  return (
    <>
      {embedded ? (
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <KeyRoundIcon className="size-4" />
                Agent secret values
              </CardTitle>
              <CardDescription>
                Create bindings and set, rotate, reveal, or delete provider values. Reveal requires
                secret.read plus recent step-up authentication and auto-hides after 30 seconds.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">{values}</CardContent>
          </Card>
          {bundles}
        </div>
      ) : (
        <Dialog open={open} onOpenChange={handleOpenChange}>
          <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-3xl">
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <KeyRoundIcon className="size-4" />
                Secrets — {envSpecName}
              </DialogTitle>
              <DialogDescription>
                Manage durable secret bindings, provider values, and reusable bundles for this
                agent. Values are disclosed only through the audited Reveal action.
              </DialogDescription>
            </DialogHeader>

            <div className="space-y-4">{values}</div>
            {bundles}
          </DialogContent>
        </Dialog>
      )}

      <AlertDialog open={deleteTarget !== null} onOpenChange={(o) => !o && setDeleteTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete secret value?</AlertDialogTitle>
            <AlertDialogDescription>
              This deletes the stored value for{" "}
              <span className="font-mono font-medium">{deleteTarget?.envVar}</span> from the secret
              store. The ref stays on the spec; runs will fail their preflight until a new value is
              set.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={onConfirmDelete}>Delete</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      <ConfirmDialog
        open={removeRefTarget !== null}
        onOpenChange={(next) => {
          if (!next) setRemoveRefTarget(null);
        }}
        title={`Remove ${removeRefTarget?.envVar ?? "secret"} binding?`}
        description="The agent will stop receiving this binding. Its provider-side value is retained and can be rebound later."
        confirmLabel="Remove binding"
        destructive
        onConfirm={async () => {
          if (removeRefTarget) await onRemoveRef(removeRefTarget);
        }}
      />
    </>
  );
}
