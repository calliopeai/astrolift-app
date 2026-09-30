"use client";

import { useTranslations } from "next-intl";

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

import { AgentSecretReadError } from "./AgentSecretReadError";

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
  error,
  onRetry,
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
  const t = useTranslations("agentSecrets.values");
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
          {t("state.error")}
        </Badge>
      );
    }
    return r.exists ? (
      <Badge>{t("state.set")}</Badge>
    ) : (
      <Badge variant="outline">{t("state.missing")}</Badge>
    );
  }

  const values = (
    <>
      {runtimeSettings}
      {error ? (
        <AgentSecretReadError label={t("valuesHeader")} message={error.message} onRetry={onRetry} />
      ) : (
        <>
          <div className="grid gap-2 rounded-md border p-3 md:grid-cols-[1fr_1.5fr_auto]">
            <Input
              className="font-mono"
              placeholder="ENV_VAR"
              value={refEnvVar}
              onChange={(event) => setRefEnvVar(event.target.value)}
            />
            <Input
              className="font-mono"
              placeholder={t("refPlaceholder", { uri: `${refNamespace}/token` })}
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
              {t("upsertRef")}
            </Button>
          </div>

          {loading && rows.length === 0 ? (
            <div className="text-muted-foreground flex items-center gap-2 py-6 text-sm">
              <Loader2Icon className="size-4 animate-spin" />
              {t("checkingStatus")}
            </div>
          ) : rows.length === 0 ? (
            <p className="text-muted-foreground py-6 text-sm">{t("noDirectRefs")}</p>
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
                      placeholder={t(r.exists ? "rotatePlaceholder" : "setPlaceholder")}
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
                      {t(r.exists ? "rotate" : "set")}
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      aria-label={t(reveals[r.envVar] ? "hideNamed" : "revealNamed", {
                        name: r.envVar,
                      })}
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
                      {t(reveals[r.envVar] ? "hide" : "reveal")}
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      aria-label={t("deleteNamed", { name: r.envVar })}
                      onClick={() => setDeleteTarget(r)}
                      disabled={pending.has(r.envVar) || busyVar === r.envVar || !r.exists}
                    >
                      <Trash2Icon className="size-4" />
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      aria-label={t("removeNamed", { name: r.envVar })}
                      onClick={() => setRemoveRefTarget(r)}
                      disabled={
                        pending.has(`remove:${r.envVar}`) || busyVar === `remove:${r.envVar}`
                      }
                    >
                      <UnlinkIcon className="size-4" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
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
                {t("valuesHeader")}
              </CardTitle>
              <CardDescription>{t("intro")}</CardDescription>
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
                {t("dialogTitle", { name: envSpecName })}
              </DialogTitle>
              <DialogDescription>{t("dialogDescription")}</DialogDescription>
            </DialogHeader>

            <div className="space-y-4">{values}</div>
            {bundles}
          </DialogContent>
        </Dialog>
      )}

      <AlertDialog open={deleteTarget !== null} onOpenChange={(o) => !o && setDeleteTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("deleteValueTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t.rich("deleteNamedDescription", {
                name: deleteTarget?.envVar ?? "",
                identifier: (chunks) => <span className="font-mono font-medium">{chunks}</span>,
              })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={onConfirmDelete}>{t("delete")}</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      <ConfirmDialog
        open={removeRefTarget !== null}
        onOpenChange={(next) => {
          if (!next) setRemoveRefTarget(null);
        }}
        title={t("removeTitle", { name: removeRefTarget?.envVar ?? "" })}
        description={t("removeDescription")}
        confirmLabel={t("removeBinding")}
        destructive
        onConfirm={async () => {
          if (removeRefTarget) await onRemoveRef(removeRefTarget);
        }}
      />
    </>
  );
}
