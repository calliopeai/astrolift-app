"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  CheckCircle2Icon,
  InfoIcon,
  Loader2Icon,
  Trash2Icon,
  WebhookIcon,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { CopyBadge } from "@/components/CopyBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { CREATE_AGENT_TRIGGER, UNBIND_AGENT_TRIGGER } from "@/graphql/agents/agents.mutations";
import { AGENT_TRIGGERS } from "@/graphql/agents/agents.queries";
import type {
  AgentTriggersData,
  AgentTriggersVars,
  AstroliftAgentTrigger,
  AstroliftAgentTriggerResult,
} from "@/graphql/agents/agents.types";
import { formatRelativeAge } from "@/lib/format";

// Hand-rolled write envelopes, matching how the agents area types its mutation
// responses inline (see control-content's UpdateResp / dispatch-tab's
// RunAgentResp) rather than consuming codegen op-types. Both resolve to the flat
// AstroliftAgentTriggerResult (ok + message — no errors[] list).
interface CreateTriggerResp {
  createAgentTrigger: AstroliftAgentTriggerResult;
}
interface UnbindTriggerResp {
  unbindAgentTrigger: AstroliftAgentTriggerResult;
}

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
 */
export function TriggerBindingEditor({
  agentSlug,
  agentName,
  orgId,
}: {
  agentSlug: string;
  agentName: string;
  orgId: string;
}) {
  const [scmRepo, setScmRepo] = React.useState<string>("");
  const [branchPattern, setBranchPattern] = React.useState<string>("");
  const [mappingText, setMappingText] = React.useState<string>("");
  const [mappingError, setMappingError] = React.useState<string | null>(null);
  // The just-created binding's one-time reveal (endpoint + signingSecret), held
  // until the operator dismisses it — the secret is never returned again.
  const [reveal, setReveal] = React.useState<AstroliftAgentTriggerResult | null>(null);
  // The binding pending removal — drives the confirm dialog.
  const [pendingRemove, setPendingRemove] = React.useState<AstroliftAgentTrigger | null>(null);

  const { data, loading } = useQuery<AgentTriggersData, AgentTriggersVars>(AGENT_TRIGGERS, {
    variables: { orgId, agentSlug },
    skip: !orgId,
    fetchPolicy: "cache-and-network",
  });
  const triggers = data?.agentTriggers ?? [];

  // Refetch the list after either write so a bind/unbind reflects immediately.
  // Variables must match the watched query exactly for Apollo to refetch it.
  const refetchQueries = orgId ? [{ query: AGENT_TRIGGERS, variables: { orgId, agentSlug } }] : [];
  const [createTrigger, { loading: creating }] = useMutation<CreateTriggerResp>(
    CREATE_AGENT_TRIGGER,
    { refetchQueries }
  );
  const [unbindTrigger] = useMutation<UnbindTriggerResp>(UNBIND_AGENT_TRIGGER, { refetchQueries });

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
        setMappingError("Input mapping must be a JSON object of key → payload-path.");
        return false;
      }
      setMappingError(null);
      return true;
    } catch {
      setMappingError("Invalid JSON — check syntax.");
      return false;
    }
  }

  const scmRepoValid = scmRepo.trim().length > 0;
  const canCreate = !creating && scmRepoValid && mappingError === null;

  async function handleCreate() {
    if (!canCreate) return;
    if (!validateMapping(mappingText)) return;
    const t = mappingText.trim();
    const inputMapping = t ? (JSON.parse(t) as Record<string, unknown>) : null;
    try {
      const { data: res } = await createTrigger({
        variables: {
          agentSlug,
          scmRepo: scmRepo.trim(),
          branchPattern: branchPattern.trim(),
          inputMapping,
        },
      });
      const result = res?.createAgentTrigger;
      if (result?.ok) {
        toast.success(`Trigger bound for ${agentName}`);
        setReveal(result);
        setScmRepo("");
        setBranchPattern("");
        setMappingText("");
        setMappingError(null);
      } else {
        toast.error("Couldn't bind trigger", { description: result?.message ?? "Unknown error" });
      }
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      toast.error("Couldn't bind trigger", { description: message });
    }
  }

  return (
    <section className="space-y-4 rounded-md border p-4">
      {/* What a trigger does + where delivery history lives. */}
      <div className="bg-muted/30 text-muted-foreground flex items-start gap-2 rounded-md border border-dashed p-3 text-xs">
        <InfoIcon className="mt-0.5 size-4 shrink-0" />
        <span>
          A trigger dispatches this agent when a push matching the source repo and branch pattern
          arrives, applying the input mapping to the webhook payload. Pausing the agent (below)
          halts dispatch without dropping the binding. Delivery history lives on the{" "}
          <Link
            href={`/agents/${encodeURIComponent(agentSlug)}/webhooks`}
            className="text-foreground font-medium underline underline-offset-2"
          >
            Webhooks tab
          </Link>
          .
        </span>
      </div>

      {/* One-time signing-secret reveal after a successful bind — mirrors the
          webhooks surface's reveal panel. */}
      {reveal && (
        <div className="border-success-border bg-success/5 space-y-3 rounded-md border p-4">
          <div className="flex items-center gap-2">
            <CheckCircle2Icon className="text-success-fg size-4" />
            <p className="text-sm font-medium">Trigger bound — save the signing secret</p>
          </div>
          <p className="text-muted-foreground text-xs">
            The signing secret is shown once and never again. Configure it and the endpoint URL in
            the source repo&apos;s webhook settings so the platform can verify deliveries.
          </p>
          {reveal.endpoint && (
            <div className="space-y-1">
              <Label className="text-xs">Endpoint</Label>
              <div className="flex items-center gap-2">
                <code className="bg-background flex-1 rounded-md border px-3 py-2 font-mono text-xs break-all">
                  {reveal.endpoint}
                </code>
                <CopyBadge value={reveal.endpoint} label="Copy" toastMessage="Endpoint copied" />
              </div>
            </div>
          )}
          {reveal.signingSecret && (
            <div className="space-y-1">
              <Label className="text-xs">Signing secret</Label>
              <div className="flex items-center gap-2">
                <code className="bg-background flex-1 rounded-md border px-3 py-2 font-mono text-xs break-all">
                  {reveal.signingSecret}
                </code>
                <CopyBadge
                  value={reveal.signingSecret}
                  label="Copy"
                  toastMessage="Signing secret copied"
                />
              </div>
            </div>
          )}
          <div className="flex justify-end">
            <Button size="sm" variant="ghost" onClick={() => setReveal(null)}>
              Dismiss
            </Button>
          </div>
        </div>
      )}

      {/* Bound triggers list. */}
      <div className="space-y-2">
        <Label>Bound triggers</Label>
        {/* `!orgId` (the #1022 cold-load race) skips the query, so treat it as
            pending too — otherwise the empty state flashes before it resolves. */}
        {(loading || !orgId) && triggers.length === 0 ? (
          <Skeleton className="h-16 w-full" />
        ) : triggers.length === 0 ? (
          <p className="text-muted-foreground rounded-md border border-dashed p-4 text-xs">
            No triggers bound yet. Bind one below to dispatch this agent on a matching push.
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
                      <span className="font-mono text-sm break-all">{t.scmRepo || "any repo"}</span>
                      <Badge variant="outline" className="font-normal">
                        {t.branchPattern || "any branch"}
                      </Badge>
                      <Badge variant={t.enabled ? "secondary" : "outline"}>
                        {t.enabled ? "Enabled" : "Disabled"}
                      </Badge>
                    </div>
                    <div className="text-muted-foreground flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
                      <span className="break-all">{t.endpoint}</span>
                      {mappings > 0 && (
                        <span>
                          {mappings} input mapping{mappings === 1 ? "" : "s"}
                        </span>
                      )}
                      <span>Bound {formatRelativeAge(t.createdAt)}</span>
                      {t.lastTriggeredAt && (
                        <span>Last fired {formatRelativeAge(t.lastTriggeredAt)}</span>
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
                    Remove
                  </Button>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {/* Bind a new trigger. */}
      <div className="space-y-3 border-t pt-4">
        <Label>Bind a new trigger</Label>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="trigger-scm-repo" className="text-xs">
              Source repo
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
              Branch pattern
            </Label>
            <Input
              id="trigger-branch-pattern"
              placeholder="main (blank = any branch)"
              value={branchPattern}
              onChange={(e) => setBranchPattern(e.target.value)}
              className="font-mono text-sm"
            />
          </div>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="trigger-input-mapping" className="text-xs">
            Input mapping (optional)
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
            <p className="text-muted-foreground text-xs">
              A JSON object mapping each agent input key to a path in the webhook payload. Leave
              blank to pass the raw payload.
            </p>
          )}
        </div>
        <div className="flex justify-end">
          <Button onClick={handleCreate} disabled={!canCreate}>
            {creating ? (
              <Loader2Icon className="size-4 animate-spin" />
            ) : (
              <WebhookIcon className="size-4" />
            )}
            Bind trigger
          </Button>
        </div>
      </div>

      <ConfirmDialog
        open={pendingRemove !== null}
        onOpenChange={(o) => {
          if (!o) setPendingRemove(null);
        }}
        title="Remove trigger?"
        description={
          pendingRemove ? (
            <>
              Unbinds <span className="font-mono">{pendingRemove.scmRepo || "any repo"}</span> from{" "}
              {agentName}. The webhook stops dispatching this agent. This can&apos;t be undone —
              rebinding issues a new endpoint and signing secret.
            </>
          ) : null
        }
        confirmLabel="Remove trigger"
        destructive
        onConfirm={async () => {
          if (!pendingRemove) return;
          const { data: res } = await unbindTrigger({ variables: { slug: pendingRemove.slug } });
          const result = res?.unbindAgentTrigger;
          if (!result?.ok) {
            throw new Error(result?.message ?? "Couldn't remove trigger");
          }
          toast.success("Trigger removed");
        }}
      />
    </section>
  );
}
