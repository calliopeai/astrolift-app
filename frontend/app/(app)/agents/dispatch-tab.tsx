"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  ArrowRightIcon,
  BotIcon,
  ChevronDownIcon,
  ClockIcon,
  InfoIcon,
  KeyRoundIcon,
  Loader2Icon,
  RepeatIcon,
  SettingsIcon,
  WebhookIcon,
  ZapIcon,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import { AgentSecretsDialog } from "@/app/(app)/agents/agent-secrets-dialog";
import { EmptyState } from "@/components/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { RUN_AGENT } from "@/graphql/agents/agents.mutations";
import {
  LIST_AGENT_ENVIRONMENT_SPECS,
  LIST_AGENT_FLEET,
  LIST_AGENT_TASKS,
} from "@/graphql/agents/agents.queries";
import type {
  AgentRunMode,
  AstroliftAgentEnvironmentSpec,
  AstroliftAgentListItem,
} from "@/graphql/agents/agents.types";
import { formatRelativeAge } from "@/lib/format";
import { cn } from "@/lib/utils";

// The runAstroliftAgent envelope (id + status of the created AgentTask). Typed
// inline — the agents area hand-rolls its GraphQL response shapes rather than
// consuming codegen op-types (see graphql/agents/*.types.ts).
interface RunAgentResp {
  runAstroliftAgent: {
    ok: boolean;
    errors: { code: string; message: string; field: string | null }[];
    data: { id: string; status: string; createdAt: string } | null;
  };
}
interface AgentFleetResp {
  agentFleet: AstroliftAgentListItem[];
}
interface EnvSpecsResp {
  agentEnvironmentSpecs: AstroliftAgentEnvironmentSpec[];
}

// The four run modes, mirroring the backend `AgentRunMode` enum (UPPERCASE wire
// form: ONCE / LOOP / SCHEDULE / TRIGGER — see astrolift_agents.schema.types).
// Only ONCE is an immediate-dispatch action here: `runAstroliftAgent` fires a
// single Once run and accepts no run_mode argument. LOOP / SCHEDULE / TRIGGER
// are the agent's PERSISTENT cadence, configured on the agent's Control tab
// (updateAgentRunSpec), so selecting one routes the operator there rather than
// pretending a Once-only mutation can fire it.
type ModeMeta = {
  value: AgentRunMode;
  label: string;
  description: string;
  icon: typeof ZapIcon;
};
const RUN_MODES: ModeMeta[] = [
  {
    value: "ONCE",
    label: "Once",
    description: "Dispatch a single run right now.",
    icon: ZapIcon,
  },
  {
    value: "SCHEDULE",
    label: "Schedule",
    description: "Run automatically on a cron cadence.",
    icon: ClockIcon,
  },
  {
    value: "LOOP",
    label: "Loop",
    description: "Re-dispatch continuously up to a concurrency cap.",
    icon: RepeatIcon,
  },
  {
    value: "TRIGGER",
    label: "Trigger",
    description: "Run when a bound webhook or event fires.",
    icon: WebhookIcon,
  },
];

// The agent's configured cadence, as a compact human label for the context
// strip under the picker. Reads the lowercase stored run_family / run_mode off
// the fleet row (the same values the Registry tab renders).
function cadenceLabel(agent: AstroliftAgentListItem): string {
  if ((agent.runFamily ?? "").toLowerCase() === "service") return "Service (always-on)";
  switch ((agent.runMode ?? "").toLowerCase()) {
    case "schedule":
      return agent.runCronExpression ? `Schedule · ${agent.runCronExpression}` : "Schedule";
    case "loop":
      return "Loop";
    case "trigger":
      return "Trigger";
    default:
      return "Once (manual)";
  }
}

function titleCase(value: string): string {
  if (!value) return "";
  return value
    .replace(/[_-]+/g, " ")
    .split(" ")
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(" ");
}

// Parse the optional timeout field to a positive integer of seconds, or null
// (the no-override case — the backend defaults to its AgentTask timeout).
function parseTimeout(text: string): number | null {
  const t = text.trim();
  if (!t) return null;
  const n = Number(t);
  return Number.isInteger(n) && n > 0 ? n : null;
}

/**
 * The fleet-level Dispatch command center (#1105).
 *
 * Distinct from the per-agent Run tab (which dispatches THIS agent): this tab
 * dispatches ANY registered agent without navigating to it first. It fires a
 * Once run via `runAstroliftAgent` with the full input that mutation accepts —
 * `triggerPayload` (prompt / JSON inputs), plus the Advanced `environmentSpecId`
 * override and `timeoutSeconds`. The created AgentTask then surfaces on the
 * Active / History tabs and drills into the run detail view.
 */
export function DispatchTab({ orgId }: { orgId: string }) {
  // Pre-registered agents (the org fleet) populate the picker; the richer fleet
  // row (vs. raw workloads) carries each agent's configured cadence for the
  // context strip. Environment specs back the Advanced override.
  const { data: fleetData, loading: fleetLoading } = useQuery<AgentFleetResp>(LIST_AGENT_FLEET, {
    variables: { orgId },
    skip: !orgId,
    fetchPolicy: "cache-and-network",
  });
  const { data: envData } = useQuery<EnvSpecsResp>(LIST_AGENT_ENVIRONMENT_SPECS, {
    variables: { orgId },
    skip: !orgId,
    fetchPolicy: "cache-and-network",
  });
  const agents = React.useMemo(() => fleetData?.agentFleet ?? [], [fleetData?.agentFleet]);
  const envSpecs = envData?.agentEnvironmentSpecs ?? [];

  const [selectedSlug, setSelectedSlug] = React.useState<string>("");
  const [runMode, setRunMode] = React.useState<AgentRunMode>("ONCE");
  const [payloadKind, setPayloadKind] = React.useState<"prompt" | "json">("prompt");
  const [promptText, setPromptText] = React.useState<string>("");
  const [jsonText, setJsonText] = React.useState<string>("");
  const [jsonError, setJsonError] = React.useState<string | null>(null);
  const [envSpecId, setEnvSpecId] = React.useState<string>("");
  const [timeoutText, setTimeoutText] = React.useState<string>("");
  const [advancedOpen, setAdvancedOpen] = React.useState<boolean>(false);
  const [confirmOpen, setConfirmOpen] = React.useState<boolean>(false);
  // The most recent dispatch, surfaced as a "view run" banner + drill-down link.
  const [lastDispatched, setLastDispatched] = React.useState<{ id: string; name: string } | null>(
    null
  );

  const selectedAgent = agents.find((a) => a.slug === selectedSlug) ?? null;
  const selectedSpec = envSpecs.find((s) => s.id === envSpecId) ?? null;

  // Refetch the fleet task lists on success so the new run shows on the Active
  // tab without a reload (the query document is shared by Active + History).
  const [runAgent, { loading: dispatching }] = useMutation<RunAgentResp>(RUN_AGENT, {
    refetchQueries: [LIST_AGENT_TASKS],
  });

  function validateJson(value: string): boolean {
    if (!value.trim()) {
      setJsonError(null);
      return true;
    }
    try {
      JSON.parse(value);
      setJsonError(null);
      return true;
    } catch {
      setJsonError("Invalid JSON — check syntax");
      return false;
    }
  }

  // Build the JSON trigger payload from the active editor. Prompt mode wraps the
  // freeform text as `{ prompt }`; JSON mode parses the raw object. Empty → null
  // (the no-payload manual case the backend expects). Throws on invalid JSON.
  function buildTriggerPayload(): unknown | null {
    if (payloadKind === "prompt") {
      const t = promptText.trim();
      return t ? { prompt: t } : null;
    }
    const t = jsonText.trim();
    return t ? JSON.parse(t) : null;
  }

  const timeoutTrimmed = timeoutText.trim();
  const timeoutValid =
    timeoutTrimmed.length === 0 || (/^\d+$/.test(timeoutTrimmed) && Number(timeoutTrimmed) > 0);

  function resetSelection(slug: string) {
    setSelectedSlug(slug);
    setLastDispatched(null);
  }

  function openConfirm() {
    if (!selectedAgent || runMode !== "ONCE") return;
    if (payloadKind === "json" && !validateJson(jsonText)) return;
    if (!timeoutValid) return;
    setConfirmOpen(true);
  }

  async function handleDispatch() {
    if (!selectedAgent) return;
    const name = selectedAgent.name;
    let payload: unknown | null;
    try {
      payload = buildTriggerPayload();
    } catch {
      setJsonError("Invalid JSON — check syntax");
      setConfirmOpen(false);
      return;
    }
    try {
      const { data } = await runAgent({
        variables: {
          input: {
            agentSlug: selectedAgent.slug,
            triggerPayload: payload,
            // "" (Agent default) → null; the resolver falls back to the
            // workload's own image/runtime when no spec is pinned.
            environmentSpecId: envSpecId || null,
            timeoutSeconds: parseTimeout(timeoutText),
          },
        },
      });
      const result = data?.runAstroliftAgent;
      if (!result?.ok) {
        throw new Error(result?.errors?.[0]?.message ?? "Dispatch failed");
      }
      toast.success(`Dispatched ${name}`);
      setConfirmOpen(false);
      if (result.data) setLastDispatched({ id: result.data.id, name });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(`Couldn't dispatch ${name}`, { description: message });
      setConfirmOpen(false);
    }
  }

  // A short preview of the payload the confirm dialog echoes back.
  const payloadPreview =
    payloadKind === "prompt"
      ? promptText.trim() || "None"
      : jsonText.trim() || "None";

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Dispatch a run</CardTitle>
        <p className="text-muted-foreground text-sm">
          Fire an on-demand run of any registered agent. The run appears on the Active tab and
          drills into its own detail view.
        </p>
      </CardHeader>
      <CardContent className="space-y-6">
        {fleetLoading && agents.length === 0 ? (
          <div className="space-y-3">
            <Skeleton className="h-9 w-full max-w-md" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : agents.length === 0 ? (
          <EmptyState
            icon={<BotIcon className="size-5" />}
            title="No agents registered"
            description="Register an agent repo to scan it for agent manifests. Once an agent is registered you can dispatch runs of it here."
            actionHref="/agents/new"
            actionLabel="Register an agent repo"
          />
        ) : (
          <>
            {lastDispatched && (
              <div className="border-success-fg/30 bg-success-fg/5 flex flex-wrap items-center justify-between gap-3 rounded-md border p-3 text-sm">
                <span className="inline-flex items-center gap-2">
                  <ZapIcon className="text-success-fg size-4" />
                  Dispatched <span className="font-medium">{lastDispatched.name}</span> — it will
                  appear on the Active tab.
                </span>
                <Button asChild size="sm" variant="outline">
                  <Link href={`/agents/runs/${encodeURIComponent(lastDispatched.id)}`}>
                    View run
                  </Link>
                </Button>
              </div>
            )}

            {/* Agent picker + configured-cadence context strip. */}
            <div className="space-y-1.5">
              <Label htmlFor="dispatch-agent">Agent</Label>
              <Select value={selectedSlug} onValueChange={resetSelection}>
                <SelectTrigger id="dispatch-agent" className="max-w-md">
                  <SelectValue placeholder="Select a registered agent…" />
                </SelectTrigger>
                <SelectContent>
                  {agents.map((a) => (
                    <SelectItem key={a.id} value={a.slug}>
                      {a.name}{" "}
                      <span className="text-muted-foreground">
                        ({a.projectSlug}/{a.appSlug})
                      </span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {selectedAgent && (
                <div className="text-muted-foreground flex flex-wrap items-center gap-2 pt-1 text-xs">
                  <span>Configured cadence:</span>
                  <Badge variant="outline" className="font-normal">
                    {cadenceLabel(selectedAgent)}
                  </Badge>
                  {selectedAgent.runPaused && <Badge variant="secondary">Paused</Badge>}
                  {selectedAgent.lastRunAt && (
                    <span title={selectedAgent.lastRunAt}>
                      Last run {formatRelativeAge(selectedAgent.lastRunAt)}
                      {selectedAgent.lastRunStatus
                        ? ` · ${titleCase(selectedAgent.lastRunStatus)}`
                        : ""}
                    </span>
                  )}
                </div>
              )}
            </div>

            {selectedAgent && (
              <>
                {/* Run-mode selector — the full matrix; only Once fires here. */}
                <div className="space-y-2">
                  <Label>Run mode</Label>
                  <div className="grid gap-2 sm:grid-cols-2">
                    {RUN_MODES.map((m) => (
                      <ModeCard
                        key={m.value}
                        meta={m}
                        selected={runMode === m.value}
                        onSelect={() => setRunMode(m.value)}
                      />
                    ))}
                  </div>
                </div>

                {runMode === "ONCE" ? (
                  <OnceForm
                    payloadKind={payloadKind}
                    onPayloadKindChange={setPayloadKind}
                    promptText={promptText}
                    onPromptChange={setPromptText}
                    jsonText={jsonText}
                    jsonError={jsonError}
                    onJsonChange={(v) => {
                      setJsonText(v);
                      if (jsonError) validateJson(v);
                    }}
                    onJsonBlur={() => validateJson(jsonText)}
                    advancedOpen={advancedOpen}
                    onAdvancedOpenChange={setAdvancedOpen}
                    envSpecId={envSpecId}
                    onEnvSpecChange={setEnvSpecId}
                    envSpecs={envSpecs}
                    timeoutText={timeoutText}
                    onTimeoutChange={setTimeoutText}
                    timeoutValid={timeoutValid}
                    dispatching={dispatching}
                    canDispatch={
                      !dispatching &&
                      timeoutValid &&
                      !(payloadKind === "json" && jsonError !== null)
                    }
                    onDispatch={openConfirm}
                  />
                ) : (
                  <CadenceRedirect agent={selectedAgent} mode={runMode} />
                )}
              </>
            )}
          </>
        )}
      </CardContent>

      {/* Confirm + fire. */}
      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Confirm dispatch</DialogTitle>
            <DialogDescription>Fire a single run of this agent now.</DialogDescription>
          </DialogHeader>
          {selectedAgent && (
            <div className="space-y-2 text-sm">
              <ConfirmRow label="Agent">
                {selectedAgent.name}{" "}
                <span className="text-muted-foreground font-mono text-xs">
                  {selectedAgent.slug}
                </span>
              </ConfirmRow>
              <ConfirmRow label="Mode">Once (immediate)</ConfirmRow>
              <ConfirmRow label="Inputs">
                <span className="line-clamp-2 font-mono text-xs">{payloadPreview}</span>
              </ConfirmRow>
              <ConfirmRow label="Environment">
                {selectedSpec ? selectedSpec.name : "Agent default"}
              </ConfirmRow>
              <ConfirmRow label="Timeout">
                {parseTimeout(timeoutText) ? `${parseTimeout(timeoutText)}s` : "Default"}
              </ConfirmRow>
            </div>
          )}
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="outline" onClick={() => setConfirmOpen(false)}>
              Cancel
            </Button>
            <Button onClick={handleDispatch} disabled={dispatching}>
              {dispatching && <Loader2Icon className="size-4 animate-spin" />}
              <ZapIcon className="size-4" />
              Confirm dispatch
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Once-mode form — payload + advanced + fire (the runAstroliftAgent surface).
// ---------------------------------------------------------------------------

interface OnceFormProps {
  payloadKind: "prompt" | "json";
  onPayloadKindChange: (k: "prompt" | "json") => void;
  promptText: string;
  onPromptChange: (v: string) => void;
  jsonText: string;
  jsonError: string | null;
  onJsonChange: (v: string) => void;
  onJsonBlur: () => void;
  advancedOpen: boolean;
  onAdvancedOpenChange: (open: boolean) => void;
  envSpecId: string;
  onEnvSpecChange: (v: string) => void;
  envSpecs: AstroliftAgentEnvironmentSpec[];
  timeoutText: string;
  onTimeoutChange: (v: string) => void;
  timeoutValid: boolean;
  dispatching: boolean;
  canDispatch: boolean;
  onDispatch: () => void;
}

function OnceForm({
  payloadKind,
  onPayloadKindChange,
  promptText,
  onPromptChange,
  jsonText,
  jsonError,
  onJsonChange,
  onJsonBlur,
  advancedOpen,
  onAdvancedOpenChange,
  envSpecId,
  onEnvSpecChange,
  envSpecs,
  timeoutText,
  onTimeoutChange,
  timeoutValid,
  dispatching,
  canDispatch,
  onDispatch,
}: OnceFormProps) {
  // Sentinel for the "Agent default" option — the Select primitive can't hold
  // an empty-string value, so map it to "" in the parent's state.
  const ENV_DEFAULT = "__default__";
  const [secretsDialogOpen, setSecretsDialogOpen] = React.useState<boolean>(false);
  const selectedSpec = envSpecs.find((s) => s.id === envSpecId) ?? null;
  // secretRefs is the JSON ref list ([{uri, env_var}]); its length badges the
  // Manage-secrets button without opening the status dialog.
  const selectedSpecSecretCount = Array.isArray(selectedSpec?.secretRefs)
    ? (selectedSpec.secretRefs as unknown[]).length
    : 0;
  return (
    <div className="space-y-4">
      {/* Inputs — a prompt string or a raw JSON object (trigger_payload). */}
      <div className="space-y-1.5">
        <div className="flex items-center justify-between gap-2">
          <Label htmlFor="dispatch-input">Inputs</Label>
          <div className="inline-flex rounded-md border p-0.5 text-xs">
            <button
              type="button"
              onClick={() => onPayloadKindChange("prompt")}
              className={cn(
                "rounded px-2 py-0.5 font-medium transition",
                payloadKind === "prompt"
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              )}
            >
              Prompt
            </button>
            <button
              type="button"
              onClick={() => onPayloadKindChange("json")}
              className={cn(
                "rounded px-2 py-0.5 font-medium transition",
                payloadKind === "json"
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              )}
            >
              JSON
            </button>
          </div>
        </div>
        {payloadKind === "prompt" ? (
          <>
            <Textarea
              id="dispatch-input"
              placeholder="Describe what this run should do…"
              value={promptText}
              onChange={(e) => onPromptChange(e.target.value)}
              rows={4}
            />
            <p className="text-muted-foreground text-xs">
              Sent to the agent as{" "}
              <span className="font-mono">{`{ "prompt": … }`}</span>. Leave blank for no input.
            </p>
          </>
        ) : (
          <>
            <Textarea
              id="dispatch-input"
              placeholder='{"key": "value"}'
              value={jsonText}
              onChange={(e) => onJsonChange(e.target.value)}
              onBlur={onJsonBlur}
              className="font-mono text-sm"
              rows={5}
            />
            {jsonError ? (
              <p className="text-destructive text-xs">{jsonError}</p>
            ) : (
              <p className="text-muted-foreground text-xs">
                A JSON object of inputs, frozen on the run as its trigger payload.
              </p>
            )}
          </>
        )}
      </div>

      {/* Advanced — only the fields runAstroliftAgent actually accepts. */}
      <Collapsible open={advancedOpen} onOpenChange={onAdvancedOpenChange}>
        <CollapsibleTrigger asChild>
          <Button type="button" variant="ghost" size="sm" className="gap-1.5 px-2">
            <SettingsIcon className="size-4" />
            Advanced
            <ChevronDownIcon
              className={cn("size-4 transition-transform", advancedOpen && "rotate-180")}
            />
          </Button>
        </CollapsibleTrigger>
        <CollapsibleContent className="space-y-4 pt-3">
          <div className="space-y-1.5">
            <Label htmlFor="dispatch-env-spec">Environment spec</Label>
            <div className="flex items-center gap-2">
              <Select
                value={envSpecId || ENV_DEFAULT}
                onValueChange={(v) => onEnvSpecChange(v === ENV_DEFAULT ? "" : v)}
              >
                <SelectTrigger id="dispatch-env-spec" className="max-w-md">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ENV_DEFAULT}>Agent default</SelectItem>
                  {envSpecs.map((s) => (
                    <SelectItem key={s.id} value={s.id}>
                      {s.name}{" "}
                      <span className="text-muted-foreground">
                        ({s.runtime || s.imageTag || s.agentType})
                      </span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="gap-1.5"
                disabled={!selectedSpec}
                onClick={() => setSecretsDialogOpen(true)}
                title="Manage the secret values this spec's refs point at"
              >
                <KeyRoundIcon className="size-4" />
                Secrets
                {selectedSpecSecretCount > 0 ? ` (${selectedSpecSecretCount})` : ""}
              </Button>
            </div>
            <p className="text-muted-foreground text-xs">
              Pins the container-environment recipe (image, runtime, tools, VNC) the run launches
              into. Leave as <span className="font-medium">Agent default</span> to use the
              agent&rsquo;s own image.
            </p>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="dispatch-timeout">Timeout (seconds)</Label>
            <Input
              id="dispatch-timeout"
              type="number"
              inputMode="numeric"
              min={1}
              step={1}
              placeholder="300"
              value={timeoutText}
              onChange={(e) => onTimeoutChange(e.target.value)}
              className={cn("max-w-[10rem] font-mono", !timeoutValid && "border-destructive")}
              aria-invalid={!timeoutValid}
            />
            {!timeoutValid ? (
              <p className="text-destructive text-xs">Enter a whole number of seconds above zero.</p>
            ) : (
              <p className="text-muted-foreground text-xs">
                How long the run may take before it&rsquo;s timed out. Blank uses the platform
                default.
              </p>
            )}
          </div>

          <div className="bg-muted/30 text-muted-foreground flex items-start gap-2 rounded-md border border-dashed p-3 text-xs">
            <InfoIcon className="mt-0.5 size-4 shrink-0" />
            <span>
              Skills, container image, and CPU/memory come from the selected environment spec or the
              agent&rsquo;s manifest — the dispatch action takes no ad-hoc sizing or skill overrides.
            </span>
          </div>
        </CollapsibleContent>
      </Collapsible>

      <div className="pt-1">
        <Button disabled={!canDispatch} onClick={onDispatch}>
          {dispatching && <Loader2Icon className="size-4 animate-spin" />}
          <ZapIcon className="size-4" />
          Dispatch run
        </Button>
      </div>

      {selectedSpec && (
        <AgentSecretsDialog
          envSpecSlug={selectedSpec.slug}
          envSpecName={selectedSpec.name}
          open={secretsDialogOpen}
          onOpenChange={setSecretsDialogOpen}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Non-Once modes route to the Control tab (the persistent run-spec editor).
// runAstroliftAgent is Once-only; Loop / Schedule / Trigger are agent-wide
// cadence set via updateAgentRunSpec, which the Control tab already owns — so
// this surfaces the current setting + deep-links there rather than duplicating
// that editor or faking a dispatch the backend can't perform.
// ---------------------------------------------------------------------------

function CadenceRedirect({
  agent,
  mode,
}: {
  agent: AstroliftAgentListItem;
  mode: AgentRunMode;
}) {
  const meta = RUN_MODES.find((m) => m.value === mode);
  const isScheduleConfigured =
    (agent.runMode ?? "").toLowerCase() === "schedule" && Boolean(agent.runCronExpression);
  return (
    <div className="space-y-3 rounded-md border border-dashed p-4">
      <div className="flex items-start gap-2 text-sm">
        <InfoIcon className="text-muted-foreground mt-0.5 size-4 shrink-0" />
        <div className="space-y-1">
          <p className="font-medium">{meta?.label} is a persistent cadence</p>
          <p className="text-muted-foreground text-xs leading-relaxed">
            {meta?.description} It applies every time the platform auto-dispatches this agent, so
            it&rsquo;s configured on the agent&rsquo;s Control tab. To run the agent one time right
            now, pick <span className="font-medium">Once</span> above.
          </p>
        </div>
      </div>

      {mode === "SCHEDULE" && (
        <p className="text-muted-foreground text-xs">
          Current schedule:{" "}
          {isScheduleConfigured ? (
            <span className="text-foreground font-mono">{agent.runCronExpression}</span>
          ) : (
            <span>not scheduled yet</span>
          )}
        </p>
      )}

      <Button asChild variant="outline" size="sm">
        <Link href={`/agents/${encodeURIComponent(agent.slug)}/control`}>
          Configure on Control
          <ArrowRightIcon className="size-4" />
        </Link>
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Small presentational helpers
// ---------------------------------------------------------------------------

function ModeCard({
  meta,
  selected,
  onSelect,
}: {
  meta: ModeMeta;
  selected: boolean;
  onSelect: () => void;
}) {
  const Icon = meta.icon;
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={cn(
        "flex flex-col items-start gap-2 rounded-md border p-3 text-left transition-colors",
        selected
          ? "border-primary bg-primary/5 ring-primary/20 ring-2"
          : "border-border hover:bg-muted/50"
      )}
    >
      <Icon className={cn("size-5", selected ? "text-primary" : "text-muted-foreground")} />
      <div>
        <p className="text-sm font-medium">{meta.label}</p>
        <p className="text-muted-foreground mt-0.5 text-xs leading-relaxed">{meta.description}</p>
      </div>
    </button>
  );
}

function ConfirmRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[7rem_1fr] items-baseline gap-3">
      <span className="text-muted-foreground">{label}</span>
      <span className="min-w-0 break-words">{children}</span>
    </div>
  );
}
