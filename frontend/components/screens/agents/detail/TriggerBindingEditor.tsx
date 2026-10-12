"use client";

import { QueryError } from "@/components/QueryError";

import { CheckCircle2Icon, InfoIcon, Loader2Icon, Trash2Icon, WebhookIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { CopyBadge } from "@/components/CopyBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import type {
  AstroliftAgentTrigger,
  AstroliftAgentTriggerResult,
} from "@/graphql/agents/agents.types";
import { useFormatters } from "@/lib/i18n/formatters";

import type { useTriggerBindings } from "./use-trigger-bindings";

export type TriggerBindingEditorViewProps = ReturnType<typeof useTriggerBindings>;

// Count the key → payload-path entries an inputMapping carries, for the row
// summary. The JSON scalar comes back as an object; anything else counts as 0.
function mappingCount(mapping: unknown): number {
  return mapping && typeof mapping === "object" && !Array.isArray(mapping)
    ? Object.keys(mapping as Record<string, unknown>).length
    : 0;
}

/**
 * Trigger-binding editor (spec 33 PR-6/PR-12; #951) — the Control tab's Trigger
 * mode configuration.
 *
 * A trigger binds a WorkflowWebhook to this agent: when a push matching `scmRepo`
 * + `branchPattern` arrives, the platform dispatches the agent with `inputMapping`
 * applied to the SCM payload (payload threading landed in #930). This surface
 * lists the current bindings, binds a new one, and unbinds an existing one.
 *
 * The run-spec's `runMode=TRIGGER` + `runPaused` stay owned by the ControlContent
 * Save button around this editor — pausing halts dispatch without dropping the
 * binding, and unbinding here doesn't change the saved mode. The one-time signing
 * secret returned on create is surfaced once in a reveal panel (mirroring the
 * webhooks surface) — it's never returned again.
 *
 * Pure view; the data lives in useTriggerBindings.
 */
export function TriggerBindingEditorView({
  agentSlug,
  agentName,
  triggers,
  loading,
  error,
  onRetry,
  creating,
  onCreate,
  onUnbind,
}: TriggerBindingEditorViewProps) {
  const tr = useTranslations("agentTriggers");
  const fmt = useFormatters();
  const [scmRepo, setScmRepo] = React.useState<string>("");
  const [branchPattern, setBranchPattern] = React.useState<string>("");
  const [mappingText, setMappingText] = React.useState<string>("");
  const [mappingError, setMappingError] = React.useState<string | null>(null);
  // The just-created binding's one-time reveal (endpoint + signingSecret), held
  // until the operator dismisses it — the secret is never returned again.
  const [reveal, setReveal] = React.useState<AstroliftAgentTriggerResult | null>(null);
  // The binding pending removal — drives the confirm dialog.
  const [pendingRemove, setPendingRemove] = React.useState<AstroliftAgentTrigger | null>(null);

  // inputMapping is optional; when present it must parse as a JSON object (a
  // key → payload-path map). Empty is valid (= no mapping — the raw payload is
  // passed). Mirrors dispatch-tab's JSON-payload validation.
  function validateMapping(value: string): boolean {
    const t = value.trim();
    if (!t) {
      setMappingError(null);
      return true;
    }
    try {
      const parsed = JSON.parse(t);
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        setMappingError(tr("mappingNotObject"));
        return false;
      }
      setMappingError(null);
      return true;
    } catch {
      setMappingError(tr("invalidJson"));
      return false;
    }
  }

  const scmRepoValid = scmRepo.trim().length > 0;
  const canCreate = !creating && !loading && !error && scmRepoValid && mappingError === null;

  async function handleCreate() {
    if (!canCreate) return;
    if (!validateMapping(mappingText)) return;
    const t = mappingText.trim();
    const inputMapping = t ? (JSON.parse(t) as Record<string, unknown>) : null;
    const result = await onCreate({
      scmRepo: scmRepo.trim(),
      branchPattern: branchPattern.trim(),
      inputMapping,
    });
    if (result) {
      setReveal(result);
      setScmRepo("");
      setBranchPattern("");
      setMappingText("");
      setMappingError(null);
    }
  }

  return (
    <section className="space-y-4 rounded-md border p-4">
      {/* What a trigger does + where delivery history lives. */}
      <div className="bg-muted/30 text-muted-foreground flex items-start gap-2 rounded-md border border-dashed p-3 text-xs">
        <InfoIcon className="mt-0.5 size-4 shrink-0" />
        <span>
          {tr.rich("intro", {
            link: (chunks) => (
              <Link
                href={`/agents/${encodeURIComponent(agentSlug)}/webhooks`}
                className="text-foreground font-medium underline underline-offset-2"
              >
                {chunks}
              </Link>
            ),
          })}
        </span>
      </div>

      {/* One-time signing-secret reveal after a successful bind — mirrors the
          webhooks surface's reveal panel. */}
      {reveal && (
        <div className="border-success-border bg-success/5 space-y-3 rounded-md border p-4">
          <div className="flex items-center gap-2">
            <CheckCircle2Icon className="text-success-fg size-4" />
            <p className="text-sm font-medium">{tr("boundTitle")}</p>
          </div>
          <p className="text-muted-foreground text-xs">{tr("boundHelp")}</p>
          {reveal.endpoint && (
            <div className="space-y-1">
              <Label className="text-xs">{tr("endpoint")}</Label>
              <div className="flex items-center gap-2">
                <code className="bg-background flex-1 rounded-md border px-3 py-2 font-mono text-xs break-all">
                  {reveal.endpoint}
                </code>
                <CopyBadge
                  value={reveal.endpoint}
                  label={tr("copy")}
                  toastMessage={tr("endpointCopied")}
                />
              </div>
            </div>
          )}
          {reveal.signingSecret && (
            <div className="space-y-1">
              <Label className="text-xs">{tr("signingSecret")}</Label>
              <div className="flex items-center gap-2">
                <code className="bg-background flex-1 rounded-md border px-3 py-2 font-mono text-xs break-all">
                  {reveal.signingSecret}
                </code>
                <CopyBadge
                  value={reveal.signingSecret}
                  label={tr("copy")}
                  toastMessage={tr("signingSecretCopied")}
                />
              </div>
            </div>
          )}
          <div className="flex justify-end">
            <Button size="sm" variant="ghost" onClick={() => setReveal(null)}>
              {tr("dismiss")}
            </Button>
          </div>
        </div>
      )}

      {/* Bound triggers list. */}
      <div className="space-y-2">
        <Label>{tr("boundTriggers")}</Label>
        {error ? (
          <QueryError title={tr("loadError")} error={error} onRetry={onRetry} />
        ) : loading && triggers.length === 0 ? (
          <Skeleton className="h-16 w-full" />
        ) : triggers.length === 0 ? (
          <p className="text-muted-foreground rounded-md border border-dashed p-4 text-xs">
            {tr("empty")}
          </p>
        ) : (
          <ul className="space-y-2">
            {triggers.map((t) => {
              const mappings = mappingCount(t.inputMapping);
              return (
                <li
                  key={t.slug}
                  className="flex flex-wrap items-start justify-between gap-3 rounded-md border p-3"
                >
                  <div className="min-w-0 space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <WebhookIcon className="text-muted-foreground size-4 shrink-0" />
                      <span className="font-mono text-sm break-all">
                        {t.scmRepo || tr("anyRepo")}
                      </span>
                      <Badge variant="outline" className="font-normal">
                        {t.branchPattern || tr("anyBranch")}
                      </Badge>
                      <Badge variant={t.enabled ? "secondary" : "outline"}>
                        {t.enabled ? tr("enabled") : tr("disabled")}
                      </Badge>
                    </div>
                    <div className="text-muted-foreground flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
                      <span className="break-all">{t.endpoint}</span>
                      {mappings > 0 && <span>{tr("mappings", { count: mappings })}</span>}
                      <span>{tr("boundAgo", { age: fmt.formatRelativeTime(t.createdAt) })}</span>
                      {t.lastTriggeredAt && (
                        <span>
                          {tr("lastFired", { age: fmt.formatRelativeTime(t.lastTriggeredAt) })}
                        </span>
                      )}
                    </div>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-destructive hover:text-destructive shrink-0"
                    onClick={() => setPendingRemove(t)}
                  >
                    <Trash2Icon className="size-4" />
                    {tr("remove")}
                  </Button>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {/* Bind a new trigger. */}
      <div className="space-y-3 border-t pt-4">
        <Label>{tr("bindNew")}</Label>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="trigger-scm-repo" className="text-xs">
              {tr("sourceRepo")}
            </Label>
            <Input
              id="trigger-scm-repo"
              placeholder="owner/repo"
              value={scmRepo}
              onChange={(e) => setScmRepo(e.target.value)}
              className="font-mono text-sm"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="trigger-branch-pattern" className="text-xs">
              {tr("branchPattern")}
            </Label>
            <Input
              id="trigger-branch-pattern"
              placeholder={tr("branchPlaceholder")}
              value={branchPattern}
              onChange={(e) => setBranchPattern(e.target.value)}
              className="font-mono text-sm"
            />
          </div>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="trigger-input-mapping" className="text-xs">
            {tr("inputMapping")}
          </Label>
          <Textarea
            id="trigger-input-mapping"
            placeholder={'{"ref": "ref", "sha": "after"}'}
            value={mappingText}
            onChange={(e) => {
              setMappingText(e.target.value);
              if (mappingError) validateMapping(e.target.value);
            }}
            onBlur={() => validateMapping(mappingText)}
            className="font-mono text-sm"
            rows={4}
          />
          {mappingError ? (
            <p className="text-destructive text-xs">{mappingError}</p>
          ) : (
            <p className="text-muted-foreground text-xs">{tr("mappingHelp")}</p>
          )}
        </div>
        <div className="flex justify-end">
          <Button onClick={handleCreate} disabled={!canCreate}>
            {creating ? (
              <Loader2Icon className="size-4 animate-spin" />
            ) : (
              <WebhookIcon className="size-4" />
            )}
            {tr("bind")}
          </Button>
        </div>
      </div>

      <ConfirmDialog
        open={pendingRemove !== null}
        onOpenChange={(o) => {
          if (!o) setPendingRemove(null);
        }}
        title={tr("removeTitle")}
        description={
          pendingRemove ? (
            <>
              {tr.rich("removeDescription", {
                repo: pendingRemove.scmRepo || tr("anyRepo"),
                agent: agentName,
                code: (chunks) => <span className="font-mono">{chunks}</span>,
              })}
            </>
          ) : null
        }
        confirmLabel={tr("removeConfirm")}
        destructive
        onConfirm={async () => {
          if (!pendingRemove) return;
          await onUnbind(pendingRemove.slug);
        }}
      />
    </section>
  );
}
