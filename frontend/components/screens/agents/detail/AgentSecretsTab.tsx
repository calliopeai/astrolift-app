"use client";

import { EyeIcon, EyeOffIcon, KeyRoundIcon, LinkIcon, Trash2Icon, UnlinkIcon } from "lucide-react";
import * as React from "react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { SettingsPage } from "@/components/settings/SettingsPage";
import type { SectionSelection } from "@/components/settings/use-settings-section";
import { StatusDot } from "@/components/StatusDot";
import { Button } from "@/components/ui/button";
import { DropdownMenuItem, DropdownMenuSeparator } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Section } from "@/components/ui/section";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import type { AstroliftAgentSecretStatus } from "@/graphql/agents/agents.types";

import { SECRET_STATE_LABEL, type SecretState, secretState } from "./agent-secrets-list";
import type { AgentSecretValuesState } from "./use-agent-secrets-tab";

const STATE_DOT: Record<SecretState, "ok" | "warn" | "error"> = {
  set: "ok",
  missing: "warn",
  error: "error",
};

export type AgentSecretValuesProps = Omit<AgentSecretValuesState, "clearReveals">;

/**
 * Secrets › Values (spec 44 §5.1, §5.4): each secret ref the agent binds on
 * the embedded list, with its value's status. Add binding and Set or rotate
 * take two fields or one, so each opens a sheet; Reveal is the audited read
 * and hides itself after 30 seconds; delete and unbind ask first. Pure.
 */
export function AgentSecretValues({
  list,
  rows,
  totalCount,
  loading,
  stale,
  error,
  onRetry,
  readError,
  refNamespace,
  reveals,
  busyVar,
  onSave,
  onDeleteValue,
  onUpsertRef,
  onRemoveRef,
  onReveal,
}: AgentSecretValuesProps) {
  const [adding, setAdding] = React.useState(false);
  const [envVar, setEnvVar] = React.useState("");
  const [uri, setUri] = React.useState("");
  const [setting, setSetting] = React.useState<AstroliftAgentSecretStatus | null>(null);
  const [value, setValue] = React.useState("");
  const [deleteTarget, setDeleteTarget] = React.useState<AstroliftAgentSecretStatus | null>(null);
  const [unbindTarget, setUnbindTarget] = React.useState<AstroliftAgentSecretStatus | null>(null);

  const columns: Column<AstroliftAgentSecretStatus>[] = [
    {
      id: "envVar",
      header: "Variable",
      sortKey: "envVar",
      cell: (r) => (
        <span className="flex min-w-0 flex-col gap-0.5">
          <span className="truncate font-mono text-sm font-medium" title={r.envVar}>
            {r.envVar}
          </span>
          {reveals[r.envVar] && (
            <code className="bg-muted block min-w-0 rounded-sm px-2 py-1 font-mono text-xs [overflow-wrap:anywhere]">
              {reveals[r.envVar]}
            </code>
          )}
        </span>
      ),
    },
    {
      id: "uri",
      header: "Provider URI",
      cellClassName: "text-muted-foreground font-mono text-xs",
      cell: (r) => (
        <span className="block max-w-80 truncate" title={r.uri}>
          {r.uri}
        </span>
      ),
    },
    {
      id: "provider",
      header: "Provider",
      sortKey: "provider",
      width: "w-40",
      cell: (r) => (
        <span className="flex min-w-0 flex-col">
          <span className="truncate text-sm">{r.provider}</span>
          {!r.canReveal && r.readLimitation && (
            <span className="text-muted-foreground truncate text-xs" title={r.readLimitation}>
              {r.readLimitation}
            </span>
          )}
        </span>
      ),
    },
    {
      id: "status",
      header: "Value",
      sortKey: "exists",
      width: "w-28",
      cell: (r) => {
        const s = secretState(r);
        return (
          <span className="inline-flex items-center gap-1.5 text-sm" title={r.error ?? undefined}>
            <StatusDot status={STATE_DOT[s]} />
            {SECRET_STATE_LABEL[s]}
          </span>
        );
      },
    },
  ];

  async function submitBinding(e: React.FormEvent) {
    e.preventDefault();
    if (await onUpsertRef(envVar, uri)) {
      setEnvVar("");
      setUri("");
      setAdding(false);
    }
  }

  async function submitValue(e: React.FormEvent) {
    e.preventDefault();
    if (setting && (await onSave(setting.envVar, value))) closeValue();
  }

  // The typed value never outlives the sheet.
  function closeValue() {
    setValue("");
    setSetting(null);
  }

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <div className="flex min-w-0 flex-wrap items-start justify-between gap-3">
        <p className="text-muted-foreground min-w-0 flex-1 text-sm">
          Bind variables to provider values, then set, rotate, reveal or delete them. Reveal needs
          secret.read and a recent step-up sign-in, and hides the value after 30 seconds.
        </p>
        <Button size="sm" onClick={() => setAdding(true)}>
          <LinkIcon className="size-4" />
          Add binding
        </Button>
      </div>

      {readError && (
        <p className="text-muted-foreground text-sm [overflow-wrap:anywhere]" role="status">
          The secret store could not be read: {readError}.
        </p>
      )}

      <ListPage<AstroliftAgentSecretStatus>
        embedded
        list={list}
        label="Secrets"
        columns={columns}
        rows={rows}
        getRowId={(r) => r.envVar}
        loading={loading}
        stale={stale}
        error={error}
        onRetry={onRetry}
        totalCount={totalCount}
        empty={{
          icon: <KeyRoundIcon className="size-5" />,
          title: "No secret bindings",
          description:
            "This environment spec binds no secrets directly. Add a binding, or attach a reusable bundle under Bundles.",
        }}
        rowActions={(r) => (
          <>
            <DropdownMenuItem disabled={busyVar === r.envVar} onSelect={() => setSetting(r)}>
              <KeyRoundIcon className="size-4" />
              {r.exists ? "Rotate value" : "Set value"}
            </DropdownMenuItem>
            <DropdownMenuItem
              disabled={!r.exists || !r.canReveal || busyVar === `reveal:${r.envVar}`}
              onSelect={() => onReveal(r)}
            >
              {reveals[r.envVar] ? (
                <EyeOffIcon className="size-4" />
              ) : (
                <EyeIcon className="size-4" />
              )}
              {reveals[r.envVar] ? "Hide value" : "Reveal value"}
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              variant="destructive"
              disabled={!r.exists || busyVar === r.envVar}
              onSelect={() => setDeleteTarget(r)}
            >
              <Trash2Icon className="size-4" />
              Delete value
            </DropdownMenuItem>
            <DropdownMenuItem
              variant="destructive"
              disabled={busyVar === `remove:${r.envVar}`}
              onSelect={() => setUnbindTarget(r)}
            >
              <UnlinkIcon className="size-4" />
              Remove binding
            </DropdownMenuItem>
          </>
        )}
      />

      <Sheet open={adding} onOpenChange={setAdding}>
        <SheetContent className="flex flex-col">
          <form onSubmit={submitBinding} className="flex min-h-0 flex-1 flex-col">
            <SheetHeader>
              <SheetTitle>Add a secret binding</SheetTitle>
              <SheetDescription>
                Runs and boxes using this recipe receive the provider value as this environment
                variable. Binding the same variable again updates it.
              </SheetDescription>
            </SheetHeader>
            <div className="flex min-w-0 flex-col gap-4 px-4">
              <div className="flex min-w-0 flex-col gap-2">
                <Label htmlFor="secret-env-var">Environment variable</Label>
                <Input
                  id="secret-env-var"
                  className="font-mono"
                  placeholder="ENV_VAR"
                  value={envVar}
                  onChange={(e) => setEnvVar(e.target.value)}
                />
              </div>
              <div className="flex min-w-0 flex-col gap-2">
                <Label htmlFor="secret-uri">Provider URI</Label>
                <Input
                  id="secret-uri"
                  className="font-mono"
                  placeholder={`${refNamespace}/token`}
                  value={uri}
                  onChange={(e) => setUri(e.target.value)}
                />
                <p className="text-muted-foreground text-xs [overflow-wrap:anywhere]">
                  Under <span className="font-mono">{refNamespace}/</span>; the platform refuses a
                  ref outside it.
                </p>
              </div>
            </div>
            <SheetFooter className="mt-auto flex-row justify-end gap-2">
              <Button type="button" variant="outline" onClick={() => setAdding(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={!envVar.trim() || !uri.trim()}>
                Save binding
              </Button>
            </SheetFooter>
          </form>
        </SheetContent>
      </Sheet>

      <Sheet open={setting !== null} onOpenChange={(open) => !open && closeValue()}>
        <SheetContent className="flex flex-col">
          <form onSubmit={submitValue} className="flex min-h-0 flex-1 flex-col">
            <SheetHeader>
              <SheetTitle className="[overflow-wrap:anywhere]">
                {setting?.exists ? "Rotate" : "Set"}{" "}
                <span className="font-mono">{setting?.envVar}</span>
              </SheetTitle>
              <SheetDescription className="[overflow-wrap:anywhere]">
                Written to <span className="font-mono">{setting?.uri}</span>. The value is never
                shown again here; Reveal is the audited way to read it.
              </SheetDescription>
            </SheetHeader>
            <div className="flex min-w-0 flex-col gap-2 px-4">
              <Label htmlFor="secret-value">Value</Label>
              <Input
                id="secret-value"
                type="password"
                autoComplete="new-password"
                className="font-mono"
                value={value}
                onChange={(e) => setValue(e.target.value)}
              />
            </div>
            <SheetFooter className="mt-auto flex-row justify-end gap-2">
              <Button type="button" variant="outline" onClick={closeValue}>
                Cancel
              </Button>
              <Button type="submit" disabled={!value || busyVar === setting?.envVar}>
                {busyVar === setting?.envVar ? "Saving…" : setting?.exists ? "Rotate" : "Set"}
              </Button>
            </SheetFooter>
          </form>
        </SheetContent>
      </Sheet>

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        title={`Delete the value of ${deleteTarget?.envVar ?? "this secret"}?`}
        description="This deletes the stored value from the secret store. The binding stays; runs fail their preflight until a new value is set."
        confirmLabel="Delete value"
        destructive
        onConfirm={async () => {
          if (deleteTarget) await onDeleteValue(deleteTarget);
        }}
      />
      <ConfirmDialog
        open={unbindTarget !== null}
        onOpenChange={(open) => !open && setUnbindTarget(null)}
        title={`Remove the ${unbindTarget?.envVar ?? "secret"} binding?`}
        description="Runs and boxes using this recipe stop receiving this variable. The provider-side value is kept and can be bound again."
        confirmLabel="Remove binding"
        destructive
        onConfirm={async () => {
          if (unbindTarget) await onRemoveRef(unbindTarget);
        }}
      />
    </div>
  );
}

export interface AgentSecretsTabProps {
  section: SectionSelection;
  /** Secrets › Values (AgentSecretValues, wired). */
  values: React.ReactNode;
  /** Secrets › Bundles: the reusable bundles attached to the agent. */
  bundles: React.ReactNode;
}

/**
 * The agent's Secrets tab (spec 44 §5.2; Leo's list rule 3): its own
 * bindings and the reusable bundles are two lists, so each is a section,
 * one on screen at a time by `?section=`, and only that one mounts. Pure.
 */
export function AgentSecretsTab({ section, values, bundles }: AgentSecretsTabProps) {
  return (
    <SettingsPage
      single={section}
      sections={[
        {
          id: "values",
          title: "Values",
          content: (
            <Section title="Values" divided className="min-w-0">
              {values}
            </Section>
          ),
        },
        {
          id: "bundles",
          title: "Bundles",
          content: (
            <Section
              title="Bundles"
              description="Reusable sets of secrets, shared across agents and attached here."
              divided
              className="min-w-0"
            >
              {bundles}
            </Section>
          ),
        },
      ]}
    />
  );
}
