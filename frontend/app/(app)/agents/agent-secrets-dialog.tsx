"use client";

import { useMutation, useQuery } from "@apollo/client/react";
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
import { toast } from "sonner";

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
import {
  DELETE_AGENT_SECRET_VALUE,
  REMOVE_AGENT_SECRET_REF,
  REVEAL_AGENT_SECRET_VALUE,
  SET_AGENT_SECRET_VALUE,
  UPSERT_AGENT_SECRET_REF,
} from "@/graphql/agents/agents.mutations";
import { AGENT_ENV_SPEC_SECRET_STATUS } from "@/graphql/agents/agents.queries";
import type { AstroliftAgentSecretStatus } from "@/graphql/agents/agents.types";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";

import { ManagedModelSection } from "./managed-model-section";
import { AgentSecretBundles } from "./agent-secret-bundles";
import { VncSessionSection } from "./vnc-session-section";

interface SecretStatusResp {
  agentEnvironmentSpecSecretStatus: AstroliftAgentSecretStatus[];
}
interface SecretMutationResp {
  [key: string]: {
    ok: boolean;
    errors: { code: string; message: string; field: string | null }[];
    data: { envVar: string; uri: string; exists: boolean } | null;
  };
}
interface SecretRevealResp {
  revealAgentSecretValue: {
    ok: boolean;
    errors: { code: string; message: string; field: string | null }[];
    data: { envVar: string; uri: string; value: string; provider: string } | null;
  };
}

function firstError(errs: { message: string }[]): string {
  return errs[0]?.message ?? "unknown error";
}

/**
 * Manage the VALUES behind an env spec's secret refs (#1173). Lists each ref
 * with a Set / Missing / Error chip (from `agentEnvironmentSpecSecretStatus`)
 * and offers set/rotate (password input, cleared after submit), an explicit
 * audited Reveal action when the provider supports reads, and confirmed delete.
 */
export function AgentSecretsDialog({
  envSpecSlug,
  envSpecName,
  managedModel,
  vncEnabled,
  open,
  onOpenChange,
  embedded = false,
  showRuntimeSettings = true,
}: {
  envSpecSlug: string;
  envSpecName: string;
  managedModel: boolean;
  vncEnabled: boolean;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  embedded?: boolean;
  showRuntimeSettings?: boolean;
}) {
  const { data, loading, refetch } = useQuery<SecretStatusResp>(AGENT_ENV_SPEC_SECRET_STATUS, {
    variables: { slug: envSpecSlug },
    skip: !open || !envSpecSlug,
    fetchPolicy: "cache-and-network",
  });
  const rows = data?.agentEnvironmentSpecSecretStatus ?? [];
  // The backend refuses a ref outside agents/<org guid>/ (#1921).
  const { org } = useActiveOrg();
  const refNamespace = `agents/${org?.id ?? "<organization id>"}`;

  // Per-ref draft value (write-only; never seeded from the server).
  const [drafts, setDrafts] = React.useState<Record<string, string>>({});
  const [refEnvVar, setRefEnvVar] = React.useState("");
  const [refUri, setRefUri] = React.useState("");
  const [reveals, setReveals] = React.useState<Record<string, string>>({});
  const revealTimers = React.useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const [busyVar, setBusyVar] = React.useState<string>("");
  const [deleteTarget, setDeleteTarget] = React.useState<AstroliftAgentSecretStatus | null>(null);
  const [removeRefTarget, setRemoveRefTarget] = React.useState<AstroliftAgentSecretStatus | null>(
    null
  );

  const [setSecret] = useMutation<SecretMutationResp>(SET_AGENT_SECRET_VALUE);
  const [deleteSecret] = useMutation<SecretMutationResp>(DELETE_AGENT_SECRET_VALUE);
  const [upsertRef] = useMutation<SecretMutationResp>(UPSERT_AGENT_SECRET_REF);
  const [removeRef] = useMutation<SecretMutationResp>(REMOVE_AGENT_SECRET_REF);
  const [revealSecret] = useMutation<SecretRevealResp>(REVEAL_AGENT_SECRET_VALUE);

  React.useEffect(
    () => () => Object.values(revealTimers.current).forEach((timer) => clearTimeout(timer)),
    []
  );

  // Drop any typed-but-unsaved values as the dialog closes so a plaintext
  // value never lingers in state between opens. Done in the close handler
  // (not an effect) to avoid a cascading render.
  function handleOpenChange(next: boolean) {
    if (!next) {
      setDrafts({});
      setReveals({});
    }
    onOpenChange?.(next);
  }

  async function onSave(envVar: string) {
    const value = drafts[envVar] ?? "";
    if (!value) {
      toast.error("Enter a value first");
      return;
    }
    setBusyVar(envVar);
    try {
      const res = await setSecret({
        variables: { slug: envSpecSlug, envVar, value },
      });
      const payload = res.data?.setAgentSecretValue;
      if (!payload?.ok) {
        toast.error(`Set ${envVar} failed: ${firstError(payload?.errors ?? [])}`);
        return;
      }
      // Clear the write draft immediately; Reveal is a separate audited action.
      setDrafts((d) => ({ ...d, [envVar]: "" }));
      toast.success(`Saved ${envVar}`);
      await refetch();
    } catch (err) {
      toast.error(`Set ${envVar} failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusyVar("");
    }
  }

  async function onConfirmDelete() {
    const target = deleteTarget;
    if (!target) return;
    setBusyVar(target.envVar);
    try {
      const res = await deleteSecret({
        variables: { slug: envSpecSlug, envVar: target.envVar },
      });
      const payload = res.data?.deleteAgentSecretValue;
      if (!payload?.ok) {
        toast.error(`Delete ${target.envVar} failed: ${firstError(payload?.errors ?? [])}`);
        return;
      }
      toast.success(`Deleted ${target.envVar}`);
      await refetch();
    } catch (err) {
      toast.error(
        `Delete ${target.envVar} failed: ${err instanceof Error ? err.message : String(err)}`
      );
    } finally {
      setBusyVar("");
      setDeleteTarget(null);
    }
  }

  async function onUpsertRef() {
    if (!refEnvVar.trim() || !refUri.trim()) {
      toast.error("Environment variable and provider URI are required");
      return;
    }
    setBusyVar(`ref:${refEnvVar}`);
    try {
      const res = await upsertRef({
        variables: { slug: envSpecSlug, envVar: refEnvVar.trim(), uri: refUri.trim() },
      });
      const payload = res.data?.upsertAgentSecretRef;
      if (!payload?.ok) {
        toast.error(`Save ref failed: ${firstError(payload?.errors ?? [])}`);
        return;
      }
      toast.success(`Saved binding ${refEnvVar.trim()}`);
      setRefEnvVar("");
      setRefUri("");
      await refetch();
    } catch (err) {
      toast.error(`Save ref failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusyVar("");
    }
  }

  async function onRemoveRef(row: AstroliftAgentSecretStatus) {
    setBusyVar(`remove:${row.envVar}`);
    try {
      const res = await removeRef({ variables: { slug: envSpecSlug, envVar: row.envVar } });
      const payload = res.data?.removeAgentSecretRef;
      if (!payload?.ok) {
        throw new Error(`Remove ref failed: ${firstError(payload?.errors ?? [])}`);
      }
      toast.success(`Removed binding ${row.envVar}`);
      await refetch();
    } catch (err) {
      toast.error(`Remove ref failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusyVar("");
    }
  }

  async function onReveal(row: AstroliftAgentSecretStatus) {
    if (reveals[row.envVar]) {
      clearTimeout(revealTimers.current[row.envVar]);
      setReveals((current) => {
        const next = { ...current };
        delete next[row.envVar];
        return next;
      });
      return;
    }
    setBusyVar(`reveal:${row.envVar}`);
    try {
      const res = await revealSecret({ variables: { slug: envSpecSlug, envVar: row.envVar } });
      const payload = res.data?.revealAgentSecretValue;
      if (!payload?.ok || !payload.data) {
        toast.error(`Reveal failed: ${firstError(payload?.errors ?? [])}`);
        return;
      }
      setReveals((current) => ({ ...current, [row.envVar]: payload.data!.value }));
      revealTimers.current[row.envVar] = setTimeout(() => {
        setReveals((current) => {
          const next = { ...current };
          delete next[row.envVar];
          return next;
        });
      }, 30_000);
    } catch (err) {
      toast.error(`Reveal failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusyVar("");
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
      {showRuntimeSettings && (
        <>
          {/* Model source — a spec-level property, independent of secret refs. */}
          <ManagedModelSection envSpecSlug={envSpecSlug} managedModel={managedModel} />
          {/* Live VNC session is another independent spec-level property. */}
          <VncSessionSection envSpecSlug={envSpecSlug} vncEnabled={vncEnabled} />
        </>
      )}

      <div className="grid gap-2 rounded-md border p-3 md:grid-cols-[1fr_1.5fr_auto]">
        <Input
          className="font-mono"
          placeholder="ENV_VAR"
          value={refEnvVar}
          onChange={(event) => setRefEnvVar(event.target.value)}
        />
        <Input
          className="font-mono"
          placeholder={`Provider URI (for example sm:${refNamespace}/token)`}
          value={refUri}
          onChange={(event) => setRefUri(event.target.value)}
        />
        <Button onClick={onUpsertRef} disabled={!refEnvVar.trim() || !refUri.trim()}>
          <LinkIcon className="size-4" /> Add / update ref
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
                    if (e.key === "Enter") onSave(r.envVar);
                  }}
                  className="font-mono"
                />
                <Button
                  type="button"
                  size="sm"
                  onClick={() => onSave(r.envVar)}
                  disabled={busyVar === r.envVar || !(drafts[r.envVar] ?? "")}
                >
                  {busyVar === r.envVar && <Loader2Icon className="size-4 animate-spin" />}
                  {r.exists ? "Rotate" : "Set"}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  aria-label={`${reveals[r.envVar] ? "Hide" : "Reveal"} ${r.envVar}`}
                  onClick={() => onReveal(r)}
                  disabled={!r.exists || !r.canReveal || busyVar === `reveal:${r.envVar}`}
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
                  disabled={busyVar === r.envVar || !r.exists}
                >
                  <Trash2Icon className="size-4" />
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  aria-label={`Remove binding ${r.envVar}`}
                  onClick={() => setRemoveRefTarget(r)}
                  disabled={busyVar === `remove:${r.envVar}`}
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
          <AgentSecretBundles envSpecSlug={envSpecSlug} active={Boolean(open)} />
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
            <AgentSecretBundles envSpecSlug={envSpecSlug} active={Boolean(open)} />
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
