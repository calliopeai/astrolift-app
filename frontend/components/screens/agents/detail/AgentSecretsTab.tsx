"use client";

import { useTranslations } from "next-intl";

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

import { type SecretState, secretState } from "./agent-secrets-list";
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
  const t = useTranslations("agentSecrets.values");
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
      header: t("variable"),
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
      header: t("providerUri"),
      cellClassName: "text-muted-foreground font-mono text-xs",
      cell: (r) => (
        <span className="block max-w-80 truncate" title={r.uri}>
          {r.uri}
        </span>
      ),
    },
    {
      id: "provider",
      header: t("provider"),
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
      header: t("value"),
      sortKey: "exists",
      width: "w-28",
      cell: (r) => {
        const s = secretState(r);
        return (
          <span className="inline-flex items-center gap-1.5 text-sm" title={r.error ?? undefined}>
            <StatusDot status={STATE_DOT[s]} />
            {t(`state.${s}`)}
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
        <p className="text-muted-foreground min-w-0 flex-1 text-sm">{t("intro")}</p>
        <Button size="sm" onClick={() => setAdding(true)}>
          <LinkIcon className="size-4" />
          {t("addBinding")}
        </Button>
      </div>

      {readError && (
        <p className="text-muted-foreground text-sm [overflow-wrap:anywhere]" role="status">
          {t("storeReadError", { message: readError })}
        </p>
      )}

      <ListPage<AstroliftAgentSecretStatus>
        embedded
        list={list}
        label={t("secrets")}
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
          title: t("emptyTitle"),
          description: t("emptyDescription"),
        }}
        rowActions={(r) => (
          <>
            <DropdownMenuItem disabled={busyVar === r.envVar} onSelect={() => setSetting(r)}>
              <KeyRoundIcon className="size-4" />
              {t(r.exists ? "rotateValue" : "setValue")}
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
              {t(reveals[r.envVar] ? "hideValue" : "revealValue")}
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              variant="destructive"
              disabled={!r.exists || busyVar === r.envVar}
              onSelect={() => setDeleteTarget(r)}
            >
              <Trash2Icon className="size-4" />
              {t("deleteValue")}
            </DropdownMenuItem>
            <DropdownMenuItem
              variant="destructive"
              disabled={busyVar === `remove:${r.envVar}`}
              onSelect={() => setUnbindTarget(r)}
            >
              <UnlinkIcon className="size-4" />
              {t("removeBinding")}
            </DropdownMenuItem>
          </>
        )}
      />

      <Sheet open={adding} onOpenChange={setAdding}>
        <SheetContent className="flex flex-col">
          <form onSubmit={submitBinding} className="flex min-h-0 flex-1 flex-col">
            <SheetHeader>
              <SheetTitle>{t("addTitle")}</SheetTitle>
              <SheetDescription>{t("bindingDescription")}</SheetDescription>
            </SheetHeader>
            <div className="flex min-w-0 flex-col gap-4 px-4">
              <div className="flex min-w-0 flex-col gap-2">
                <Label htmlFor="secret-env-var">{t("environmentVariable")}</Label>
                <Input
                  id="secret-env-var"
                  className="font-mono"
                  placeholder="ENV_VAR"
                  value={envVar}
                  onChange={(e) => setEnvVar(e.target.value)}
                />
              </div>
              <div className="flex min-w-0 flex-col gap-2">
                <Label htmlFor="secret-uri">{t("providerUri")}</Label>
                <Input
                  id="secret-uri"
                  className="font-mono"
                  placeholder={`${refNamespace}/token`}
                  value={uri}
                  onChange={(e) => setUri(e.target.value)}
                />
                <p className="text-muted-foreground text-xs [overflow-wrap:anywhere]">
                  {t.rich("namespaceNotice", {
                    namespace: `${refNamespace}/`,
                    identifier: (chunks) => <span className="font-mono">{chunks}</span>,
                  })}
                </p>
              </div>
            </div>
            <SheetFooter className="mt-auto flex-row justify-end gap-2">
              <Button type="button" variant="outline" onClick={() => setAdding(false)}>
                {t("cancel")}
              </Button>
              <Button type="submit" disabled={!envVar.trim() || !uri.trim()}>
                {t("saveBinding")}
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
                {t.rich("valueTitle", {
                  action: t(setting?.exists ? "rotate" : "set"),
                  name: setting?.envVar ?? "",
                  identifier: (chunks) => <span className="font-mono">{chunks}</span>,
                })}
              </SheetTitle>
              <SheetDescription className="[overflow-wrap:anywhere]">
                {t.rich("valueWritten", {
                  uri: setting?.uri ?? "",
                  identifier: (chunks) => <span className="font-mono">{chunks}</span>,
                })}
              </SheetDescription>
            </SheetHeader>
            <div className="flex min-w-0 flex-col gap-2 px-4">
              <Label htmlFor="secret-value">{t("value")}</Label>
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
                {t("cancel")}
              </Button>
              <Button type="submit" disabled={!value || busyVar === setting?.envVar}>
                {t(busyVar === setting?.envVar ? "saving" : setting?.exists ? "rotate" : "set")}
              </Button>
            </SheetFooter>
          </form>
        </SheetContent>
      </Sheet>

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        title={t("deleteTitle", { name: deleteTarget?.envVar ?? "" })}
        description={t("deleteDescription")}
        confirmLabel={t("deleteValue")}
        destructive
        onConfirm={async () => {
          if (deleteTarget) await onDeleteValue(deleteTarget);
        }}
      />
      <ConfirmDialog
        open={unbindTarget !== null}
        onOpenChange={(open) => !open && setUnbindTarget(null)}
        title={t("removeTitle", { name: unbindTarget?.envVar ?? "" })}
        description={t("removeDescription")}
        confirmLabel={t("removeBinding")}
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
  const t = useTranslations("agentSecrets.values");
  return (
    <SettingsPage
      single={section}
      sections={[
        {
          id: "values",
          title: t("valuesTitle"),
          content: (
            <Section title={t("valuesTitle")} divided className="min-w-0">
              {values}
            </Section>
          ),
        },
        {
          id: "bundles",
          title: t("bundlesTitle"),
          content: (
            <Section
              title={t("bundlesTitle")}
              description={t("bundlesDescription")}
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
