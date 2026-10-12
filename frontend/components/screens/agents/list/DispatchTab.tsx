"use client";

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
import { useTranslations } from "next-intl";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
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
import type {
  AgentRunMode,
  AstroliftAgentEnvironmentSpec,
  AstroliftAgentListItem,
} from "@/graphql/agents/agents.types";
import { useFormatters } from "@/lib/i18n/formatters";
import { cn } from "@/lib/utils";

import type { useDispatchTab } from "./use-dispatch-tab";

/** What the secrets dialog slot needs: the pinned spec and its open state. */
export interface DispatchSecretsDialogArgs {
  spec: AstroliftAgentEnvironmentSpec;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export type DispatchTabViewProps = ReturnType<typeof useDispatchTab> & {
  /** Renders the Manage-secrets dialog for the selected environment spec. */
  renderSecretsDialog?: (args: DispatchSecretsDialogArgs) => React.ReactNode;
};

// The four run modes, mirroring the backend `AgentRunMode` enum (UPPERCASE wire
// form: ONCE / LOOP / SCHEDULE / TRIGGER — see astrolift_agents.schema.types).
// Only ONCE is an immediate-dispatch action here: `runAstroliftAgent` fires a
// single Once run and accepts no run_mode argument. LOOP / SCHEDULE / TRIGGER
// are the agent's PERSISTENT cadence, configured on the agent's Control tab
// (updateAgentRunSpec), so selecting one routes the operator there rather than
// pretending a Once-only mutation can fire it.
type ModeMeta = {
  value: AgentRunMode;
  key: "once" | "schedule" | "loop" | "trigger";
  icon: typeof ZapIcon;
};
const RUN_MODES: ModeMeta[] = [
  {
    value: "ONCE",
    key: "once",
    icon: ZapIcon,
  },
  {
    value: "SCHEDULE",
    key: "schedule",
    icon: ClockIcon,
  },
  {
    value: "LOOP",
    key: "loop",
    icon: RepeatIcon,
  },
  {
    value: "TRIGGER",
    key: "trigger",
    icon: WebhookIcon,
  },
];

// The agent's configured cadence, as a compact human label for the context
// strip under the picker. Reads the lowercase stored run_family / run_mode off
// the fleet row (the same values the Registry tab renders).
function cadenceLabel(
  agent: AstroliftAgentListItem,
  t: ReturnType<typeof useTranslations<"agentDispatch">>
): string {
  if ((agent.runFamily ?? "").toLowerCase() === "service") return t("cadence.service");
  switch ((agent.runMode ?? "").toLowerCase()) {
    case "schedule":
      return agent.runCronExpression
        ? t("cadence.scheduleCron", { cron: agent.runCronExpression })
        : t("cadence.schedule");
    case "loop":
      return t("cadence.loop");
    case "trigger":
      return t("cadence.trigger");
    default:
      return t("cadence.once");
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
 * override and `timeoutSeconds`. The created AgentTask then surfaces in
 * Agents › Runs and drills into the run detail view.
 */
export function DispatchTabView({
  agents,
  envSpecs,
  fleetLoading,
  dispatching,
  onDispatch,
  renderSecretsDialog,
  defaultAgentSlug = "",
  defaultRunMode = "ONCE",
}: DispatchTabViewProps & {
  /** Initial picker selection (stories, and `?agent=` on Run agent). */
  defaultAgentSlug?: string;
  /** Initial run mode (stories). */
  defaultRunMode?: AgentRunMode;
}) {
  const t = useTranslations("agentDispatch");
  const fmt = useFormatters();
  const [selectedSlug, setSelectedSlug] = React.useState<string>(defaultAgentSlug);
  const [runMode, setRunMode] = React.useState<AgentRunMode>(defaultRunMode);
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
      setJsonError(t("invalidJson"));
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
      setJsonError(t("invalidJson"));
      setConfirmOpen(false);
      return;
    }
    const run = await onDispatch({
      agent: selectedAgent,
      triggerPayload: payload,
      environmentSpecId: envSpecId,
      timeoutSeconds: parseTimeout(timeoutText),
    });
    setConfirmOpen(false);
    if (run) setLastDispatched({ id: run.id, name });
  }

  // A short preview of the payload the confirm dialog echoes back.
  const payloadPreview =
    payloadKind === "prompt" ? promptText.trim() || t("none") : jsonText.trim() || t("none");

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{t("title")}</CardTitle>
        <p className="text-muted-foreground text-sm">{t("description")}</p>
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
            title={t("emptyTitle")}
            description={t("emptyDescription")}
            actionHref="/agents/new"
            actionLabel={t("emptyAction")}
          />
        ) : (
          <>
            {lastDispatched && (
              <div className="border-success-fg/30 bg-success-fg/5 flex flex-wrap items-center justify-between gap-3 rounded-md border p-3 text-sm">
                <span className="inline-flex items-center gap-2">
                  <ZapIcon className="text-success-fg size-4" />
                  {t.rich("dispatched", {
                    name: lastDispatched.name,
                    strong: (chunks) => <span className="font-medium">{chunks}</span>,
                  })}
                </span>
                <Button asChild size="sm" variant="outline">
                  <Link href={`/agents/runs/${encodeURIComponent(lastDispatched.id)}`}>
                    {t("viewRun")}
                  </Link>
                </Button>
              </div>
            )}

            {/* Agent picker + configured-cadence context strip. */}
            <div className="space-y-1.5">
              <Label htmlFor="dispatch-agent">{t("agent")}</Label>
              <Select value={selectedSlug} onValueChange={resetSelection}>
                <SelectTrigger id="dispatch-agent" className="max-w-md">
                  <SelectValue placeholder={t("selectAgent")} />
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
                  <span>{t("configuredCadence")}</span>
                  <Badge variant="outline" className="font-normal">
                    {cadenceLabel(selectedAgent, t)}
                  </Badge>
                  {selectedAgent.runPaused && <Badge variant="secondary">{t("paused")}</Badge>}
                  {selectedAgent.lastRunAt && (
                    <span title={selectedAgent.lastRunAt}>
                      {t("lastRun", { age: fmt.formatRelativeTime(selectedAgent.lastRunAt) })}
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
                  <Label>{t("runMode")}</Label>
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
                    renderSecretsDialog={renderSecretsDialog}
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
            <DialogTitle>{t("confirmTitle")}</DialogTitle>
            <DialogDescription>{t("confirmDescription")}</DialogDescription>
          </DialogHeader>
          {selectedAgent && (
            <div className="space-y-2 text-sm">
              <ConfirmRow label={t("confirmAgent")}>
                {selectedAgent.name}{" "}
                <span className="text-muted-foreground font-mono text-xs">
                  {selectedAgent.slug}
                </span>
              </ConfirmRow>
              <ConfirmRow label={t("confirmMode")}>{t("confirmModeOnce")}</ConfirmRow>
              <ConfirmRow label={t("confirmInputs")}>
                <span className="line-clamp-2 font-mono text-xs">{payloadPreview}</span>
              </ConfirmRow>
              <ConfirmRow label={t("confirmEnvironment")}>
                {selectedSpec ? selectedSpec.name : t("agentDefault")}
              </ConfirmRow>
              <ConfirmRow label={t("confirmTimeout")}>
                {parseTimeout(timeoutText)
                  ? t("seconds", { seconds: parseTimeout(timeoutText) ?? 0 })
                  : t("default")}
              </ConfirmRow>
            </div>
          )}
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="outline" onClick={() => setConfirmOpen(false)}>
              {t("cancel")}
            </Button>
            <Button onClick={handleDispatch} disabled={dispatching}>
              {dispatching && <Loader2Icon className="size-4 animate-spin" />}
              <ZapIcon className="size-4" />
              {t("confirmDispatch")}
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
  renderSecretsDialog?: (args: DispatchSecretsDialogArgs) => React.ReactNode;
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
  renderSecretsDialog,
}: OnceFormProps) {
  const t = useTranslations("agentDispatch");
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
          <Label htmlFor="dispatch-input">{t("inputs")}</Label>
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
              {t("prompt")}
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
              placeholder={t("promptPlaceholder")}
              value={promptText}
              onChange={(e) => onPromptChange(e.target.value)}
              rows={4}
            />
            <p className="text-muted-foreground text-xs">
              {t.rich("promptHelp", {
                example: `{ "prompt": … }`,
                code: (chunks) => <span className="font-mono">{chunks}</span>,
              })}
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
              <p className="text-muted-foreground text-xs">{t("jsonHelp")}</p>
            )}
          </>
        )}
      </div>

      {/* Advanced — only the fields runAstroliftAgent actually accepts. */}
      <Collapsible open={advancedOpen} onOpenChange={onAdvancedOpenChange}>
        <CollapsibleTrigger asChild>
          <Button type="button" variant="ghost" size="sm" className="gap-1.5 px-2">
            <SettingsIcon className="size-4" />
            {t("advanced")}
            <ChevronDownIcon
              className={cn("size-4 transition-transform", advancedOpen && "rotate-180")}
            />
          </Button>
        </CollapsibleTrigger>
        <CollapsibleContent className="space-y-4 pt-3">
          <div className="space-y-1.5">
            <Label htmlFor="dispatch-env-spec">{t("envSpec")}</Label>
            <div className="flex items-center gap-2">
              <Select
                value={envSpecId || ENV_DEFAULT}
                onValueChange={(v) => onEnvSpecChange(v === ENV_DEFAULT ? "" : v)}
              >
                <SelectTrigger id="dispatch-env-spec" className="max-w-md">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ENV_DEFAULT}>{t("agentDefault")}</SelectItem>
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
                title={t("secretsTitle")}
              >
                <KeyRoundIcon className="size-4" />
                {t("secretsButton", { count: selectedSpecSecretCount })}
              </Button>
            </div>
            <p className="text-muted-foreground text-xs">
              {t.rich("envSpecHelp", {
                strong: (chunks) => <span className="font-medium">{chunks}</span>,
              })}
            </p>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="dispatch-timeout">{t("timeoutLabel")}</Label>
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
              <p className="text-destructive text-xs">{t("timeoutInvalid")}</p>
            ) : (
              <p className="text-muted-foreground text-xs">{t("timeoutHelp")}</p>
            )}
          </div>

          <div className="bg-muted/30 text-muted-foreground flex items-start gap-2 rounded-md border border-dashed p-3 text-xs">
            <InfoIcon className="mt-0.5 size-4 shrink-0" />
            <span>{t("advancedNote")}</span>
          </div>
        </CollapsibleContent>
      </Collapsible>

      <div className="pt-1">
        <Button disabled={!canDispatch} onClick={onDispatch}>
          {dispatching && <Loader2Icon className="size-4 animate-spin" />}
          <ZapIcon className="size-4" />
          {t("dispatchRun")}
        </Button>
      </div>

      {selectedSpec &&
        renderSecretsDialog?.({
          spec: selectedSpec,
          open: secretsDialogOpen,
          onOpenChange: setSecretsDialogOpen,
        })}
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

function CadenceRedirect({ agent, mode }: { agent: AstroliftAgentListItem; mode: AgentRunMode }) {
  const t = useTranslations("agentDispatch");
  const meta = RUN_MODES.find((m) => m.value === mode);
  const isScheduleConfigured =
    (agent.runMode ?? "").toLowerCase() === "schedule" && Boolean(agent.runCronExpression);
  return (
    <div className="space-y-3 rounded-md border border-dashed p-4">
      <div className="flex items-start gap-2 text-sm">
        <InfoIcon className="text-muted-foreground mt-0.5 size-4 shrink-0" />
        <div className="space-y-1">
          <p className="font-medium">
            {t("redirectTitle", { mode: meta ? t(`modes.${meta.key}.label`) : "" })}
          </p>
          <p className="text-muted-foreground text-xs leading-relaxed">
            {t.rich("redirectBody", {
              description: meta ? t(`modes.${meta.key}.description`) : "",
              strong: (chunks) => <span className="font-medium">{chunks}</span>,
            })}
          </p>
        </div>
      </div>

      {mode === "SCHEDULE" && (
        <p className="text-muted-foreground text-xs">
          {t("currentSchedule")}{" "}
          {isScheduleConfigured ? (
            <span className="text-foreground font-mono">{agent.runCronExpression}</span>
          ) : (
            <span>{t("notScheduled")}</span>
          )}
        </p>
      )}

      <Button asChild variant="outline" size="sm">
        <Link href={`/agents/${encodeURIComponent(agent.slug)}/configuration?section=run-mode`}>
          {t("configureRunMode")}
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
  const t = useTranslations("agentDispatch");
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
        <p className="text-sm font-medium">{t(`modes.${meta.key}.label`)}</p>
        <p className="text-muted-foreground mt-0.5 text-xs leading-relaxed">
          {t(`modes.${meta.key}.description`)}
        </p>
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
