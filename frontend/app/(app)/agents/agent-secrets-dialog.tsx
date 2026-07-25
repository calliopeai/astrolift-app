"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { KeyRoundIcon, Loader2Icon, Trash2Icon } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

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
import {
  DELETE_AGENT_SECRET_VALUE,
  SET_AGENT_SECRET_VALUE,
} from "@/graphql/agents/agents.mutations";
import { AGENT_ENV_SPEC_SECRET_STATUS } from "@/graphql/agents/agents.queries";
import type { AstroliftAgentSecretStatus } from "@/graphql/agents/agents.types";

import { ManagedModelSection } from "./managed-model-section";
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

function firstError(errs: { message: string }[]): string {
  return errs[0]?.message ?? "unknown error";
}

/**
 * Manage the VALUES behind an env spec's secret refs (#1173). Lists each ref
 * with a Set / Missing / Error chip (from `agentEnvironmentSpecSecretStatus`)
 * and offers a write-only set/rotate (password input, cleared after submit, no
 * read-back) plus a confirmed delete. The plaintext is never rendered or read
 * back — the platform never returns it.
 */
export function AgentSecretsDialog({
  envSpecSlug,
  envSpecName,
  managedModel,
  vncEnabled,
  open,
  onOpenChange,
}: {
  envSpecSlug: string;
  envSpecName: string;
  managedModel: boolean;
  vncEnabled: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { data, loading, refetch } = useQuery<SecretStatusResp>(AGENT_ENV_SPEC_SECRET_STATUS, {
    variables: { slug: envSpecSlug },
    skip: !open || !envSpecSlug,
    fetchPolicy: "cache-and-network",
  });
  const rows = data?.agentEnvironmentSpecSecretStatus ?? [];

  // Per-ref draft value (write-only; never seeded from the server).
  const [drafts, setDrafts] = React.useState<Record<string, string>>({});
  const [busyVar, setBusyVar] = React.useState<string>("");
  const [deleteTarget, setDeleteTarget] = React.useState<AstroliftAgentSecretStatus | null>(null);

  const [setSecret] = useMutation<SecretMutationResp>(SET_AGENT_SECRET_VALUE);
  const [deleteSecret] = useMutation<SecretMutationResp>(DELETE_AGENT_SECRET_VALUE);

  // Drop any typed-but-unsaved values as the dialog closes so a plaintext
  // value never lingers in state between opens. Done in the close handler
  // (not an effect) to avoid a cascading render.
  function handleOpenChange(next: boolean) {
    if (!next) setDrafts({});
    onOpenChange(next);
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
      // Clear the draft immediately — no read-back, no lingering plaintext.
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

  return (
    <>
      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <KeyRoundIcon className="size-4" />
              Secrets — {envSpecName}
            </DialogTitle>
            <DialogDescription>
              Set, rotate, and delete the values behind this spec&rsquo;s secret refs. Values are
              written straight to the install&rsquo;s secret store; they are never stored or shown
              here.
            </DialogDescription>
          </DialogHeader>

          {/* Model source — a spec-level property, independent of the secret
              refs below. When ON, runs use the cluster's cloud model provider
              via workload identity, so no ANTHROPIC_API_KEY secret is needed. */}
          <ManagedModelSection envSpecSlug={envSpecSlug} managedModel={managedModel} />

          {/* Live session — another spec-level property. When ON, runs launch on
              a watchable VNC image so operators can watch the live desktop
              session; OFF is headless (logs only). */}
          <VncSessionSection envSpecSlug={envSpecSlug} vncEnabled={vncEnabled} />

          {loading && rows.length === 0 ? (
            <div className="text-muted-foreground flex items-center gap-2 py-6 text-sm">
              <Loader2Icon className="size-4 animate-spin" />
              Checking secret status…
            </div>
          ) : rows.length === 0 ? (
            <p className="text-muted-foreground py-6 text-sm">
              This environment spec declares no secret refs. Add a ref (env var → store URI) on the
              spec first, then set its value here.
            </p>
          ) : (
            <div className="space-y-4">
              {rows.map((r) => (
                <div key={r.envVar} className="space-y-2 rounded-md border p-3">
                  <div className="flex items-center justify-between gap-2">
                    <div className="min-w-0">
                      <p className="truncate font-mono text-sm font-medium">{r.envVar}</p>
                      <p className="text-muted-foreground truncate font-mono text-xs">{r.uri}</p>
                    </div>
                    {statusBadge(r)}
                  </div>
                  <div className="flex items-center gap-2">
                    <Input
                      type="password"
                      autoComplete="off"
                      placeholder={r.exists ? "Enter a new value to rotate" : "Enter value to set"}
                      value={drafts[r.envVar] ?? ""}
                      onChange={(e) =>
                        setDrafts((d) => ({ ...d, [r.envVar]: e.target.value }))
                      }
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
                      aria-label={`Delete ${r.envVar}`}
                      onClick={() => setDeleteTarget(r)}
                      disabled={busyVar === r.envVar || !r.exists}
                    >
                      <Trash2Icon className="size-4" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </DialogContent>
      </Dialog>

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
    </>
  );
}
