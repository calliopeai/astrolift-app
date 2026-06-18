"use client";

import { useMutation } from "@apollo/client/react";
import {
  ClockIcon,
  InfoIcon,
  Loader2Icon,
  PlayIcon,
  RepeatIcon,
  SaveIcon,
  ServerIcon,
  WebhookIcon,
} from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { UPDATE_AGENT_RUN_SPEC } from "@/graphql/agents/agents.mutations";
import { LIST_AGENT_FLEET } from "@/graphql/agents/agents.queries";
import type {
  AgentRunFamily,
  AgentRunMode,
  AgentRunSpecInput,
  AstroliftAgentListItem,
  AstroliftAgentRunSpec,
} from "@/graphql/agents/agents.types";
import type { MutationResult } from "@/graphql/identity/identity.types";
import { getActiveOrgGuid } from "@/lib/identity/active-org";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Run-spec editor — Once / Schedule / Service (spec 33 PR-11)
//
// Fills the PR-9 Control-tab stub with the editor for the three modes whose
// backend landed in PR-1/PR-4. The remaining modes (Loop, Trigger, and the
// Service scheduled-scaling two-cron pair) are PR-12 — their cards render here
// as disabled "coming soon" affordances so the full run-spec model from spec
// §4 is visible, but this editor never writes their fields.
//
// Reuse (per the spec's reuse-heavy mandate):
//   - the cron text input + `naturalCronHint` read-back + `CRON_FIELD` 5-field
//     validation, cloned from `apps/new/steps/DeployStrategyStep.tsx`;
//   - the `OptionCard` card-style selector, cloned from the same file (it is an
//     inline component there, not a shared export);
//   - the bounded replica range control + numeric read-out, mirroring
//     `apps/[slug]/workloads/[workloadSlug]/scaling-card.tsx`'s manual section.
//     Note: that card SCALES a live deployed workload (live k8s poll +
//     `scaleAstroliftWorkload`); here the same control edits the run-spec's
//     declared baseline `replicas` and persists via `updateAgentRunSpec` — a
//     different, declarative write target.
//
// Net-new = the Task/Service family toggle bound to the run-spec model + the
// `updateAgentRunSpec` wiring (the case mapping, the per-mode owned-field set,
// and the error-field → control mapping).
// ---------------------------------------------------------------------------

// 5-field cron matcher + natural-language read-back, cloned verbatim from
// DeployStrategyStep so the Schedule editor sanity-checks the same way the
// app-deploy cron does (the backend re-validates via the platform cron parser).
const CRON_FIELD = /^(\S+\s+){4}\S+$/;

function naturalCronHint(expr: string): string {
  // Lightweight read-back, not a real parser. Just translates a few
  // very common patterns so an operator can sanity-check before
  // submitting.
  const e = expr.trim();
  if (!CRON_FIELD.test(e)) return "Five-field cron expression expected.";
  if (e === "0 * * * *") return "Hourly, on the hour.";
  if (e === "*/5 * * * *") return "Every 5 minutes.";
  if (e === "0 3 * * *") return "Daily at 03:00.";
  if (e === "0 0 * * 0") return "Weekly on Sunday at 00:00.";
  if (e === "0 0 1 * *") return "Monthly on the 1st at 00:00.";
  return "Custom schedule.";
}

// Service replica bounds. The run-spec `replicas` is the declared baseline the
// Deployment renderer reads; the env-max clamp is resolved live at deploy/scale
// time server-side (the mutation only enforces >= 1). We bound the slider at a
// sane editor ceiling so the control stays usable; an out-of-policy value is
// caught by the backend and surfaced as a field error.
const REPLICA_MIN = 1;
const REPLICA_MAX = 20;

// The two families and the Task modes, in spec §4 order. `comingSoon` flags the
// PR-12 modes (Loop, Trigger): rendered disabled rather than omitted so the
// operator sees the whole model and that the rest is coming, mirroring
// DeployStrategyStep's own `comingSoon` affordance.
type ModeOption = {
  value: AgentRunMode;
  label: string;
  description: string;
  icon: typeof PlayIcon;
  comingSoon?: boolean;
};

const TASK_MODES: ModeOption[] = [
  {
    value: "ONCE",
    label: "Once",
    description: "Runs only when dispatched manually from the Run tab.",
    icon: PlayIcon,
  },
  {
    value: "SCHEDULE",
    label: "Schedule",
    description: "Dispatches a run automatically on a cron schedule.",
    icon: ClockIcon,
  },
  {
    value: "LOOP",
    label: "Loop",
    description: "Re-dispatches continuously up to a concurrency cap.",
    icon: RepeatIcon,
    comingSoon: true,
  },
  {
    value: "TRIGGER",
    label: "Trigger",
    description: "Dispatches when a bound webhook or event fires.",
    icon: WebhookIcon,
    comingSoon: true,
  },
];

// Normalize the LOWERCASE stored read value (off the list row / persisted spec)
// to the UPPERCASE wire enum the editor's state + input use. Unknown values
// degrade to the safe default rather than throwing.
function toFamily(stored: string): AgentRunFamily {
  return stored.toLowerCase() === "service" ? "SERVICE" : "TASK";
}
function toMode(stored: string): AgentRunMode {
  switch (stored.toLowerCase()) {
    case "schedule":
      return "SCHEDULE";
    case "loop":
      return "LOOP";
    case "trigger":
      return "TRIGGER";
    default:
      return "ONCE";
  }
}

interface UpdateResp {
  updateAgentRunSpec: MutationResult<AstroliftAgentRunSpec>;
}

/**
 * Control tab content for an agent (spec 33 PR-11) — the run-spec editor.
 *
 * Seeds its initial state from the resolved fleet row (`runFamily` / `runMode`
 * / `runCronExpression` / `runPaused`, all lowercase). The Service replica
 * count is the one field the list row does NOT carry, so it cannot be seeded on
 * first load — see `replicasSeeded` / the inline note. After a save the
 * mutation returns the persisted `AstroliftAgentRunSpec` (which DOES include
 * `replicas`), and the editor re-seeds from that.
 */
export function ControlContent({ agent }: { agent: AstroliftAgentListItem }) {
  // --- Editor state, seeded from the resolved (lowercase) read row. --------
  const [family, setFamily] = React.useState<AgentRunFamily>(() => toFamily(agent.runFamily));
  const [mode, setMode] = React.useState<AgentRunMode>(() => toMode(agent.runMode));
  const [cron, setCron] = React.useState<string>(agent.runCronExpression ?? "");
  const [paused, setPaused] = React.useState<boolean>(Boolean(agent.runPaused));
  // Service replica baseline. The list row has no `replicas`, so we can't seed
  // the real stored value on first load — start at the floor and flag that the
  // shown value is a default until the first save reads it back.
  const [replicas, setReplicas] = React.useState<number>(REPLICA_MIN);
  const [replicasSeeded, setReplicasSeeded] = React.useState<boolean>(false);

  // Field-scoped server error (errors[].field → control). Cleared on any edit
  // and on submit so a stale message never lingers under a now-valid control.
  const [fieldError, setFieldError] = React.useState<{ field: string; message: string } | null>(
    null
  );

  // Re-seed when the resolved agent row changes identity (e.g. the parent
  // refetched after a save, or the route switched agents). Mirrors the
  // "derive state on prop change" recipe the scaling card uses for `desired`.
  const [seedKey, setSeedKey] = React.useState<string>(agent.id);
  if (seedKey !== agent.id) {
    setSeedKey(agent.id);
    setFamily(toFamily(agent.runFamily));
    setMode(toMode(agent.runMode));
    setCron(agent.runCronExpression ?? "");
    setPaused(Boolean(agent.runPaused));
    setReplicas(REPLICA_MIN);
    setReplicasSeeded(false);
    setFieldError(null);
  }

  // Refetch the fleet list after a save so the resolved row re-seeds with the
  // persisted run-spec. `updateAgentRunSpec` returns an `AstroliftAgentRunSpec`
  // — a DIFFERENT GraphQL type than the `AstroliftAgentListItem` the shell
  // header + `useAgent` read — so the mutation result does NOT normalize into
  // the list-row cache; without this refetch the header badge (run mode /
  // paused) would show stale values until the fleet query reloaded on its own.
  // Scoped to the same `orgId` `useAgent` resolves against.
  const orgId = getActiveOrgGuid() ?? "";
  const [save, { loading: saving }] = useMutation<UpdateResp>(UPDATE_AGENT_RUN_SPEC, {
    refetchQueries: orgId ? [{ query: LIST_AGENT_FLEET, variables: { orgId } }] : [],
  });

  // A PR-12 mode is selected (Loop/Trigger) → the editor can't own its config,
  // so saving is disabled with an explanatory note rather than silently
  // dropping fields.
  const isComingSoonMode = family === "TASK" && (mode === "LOOP" || mode === "TRIGGER");

  // Client-side validity (immediate feedback; the backend remains the source of
  // truth and re-checks all of this):
  //   - Task + Schedule  → cron must be a 5-field expression
  //   - Service          → replicas in [REPLICA_MIN, REPLICA_MAX]
  //   - Loop / Trigger   → not editable here (PR-12)
  const cronValid = CRON_FIELD.test(cron.trim());
  const replicasValid = replicas >= REPLICA_MIN && replicas <= REPLICA_MAX;
  const canSave =
    !saving &&
    !isComingSoonMode &&
    (family === "SERVICE" ? replicasValid : mode === "SCHEDULE" ? cronValid : true);

  function clearError() {
    if (fieldError) setFieldError(null);
  }

  // Build the input the editor owns for the chosen family/mode. Every key is
  // present (the generated input has no optionals — `avoidOptionals`), so an
  // unowned field is sent as `null`, which the partial-update backend reads as
  // "leave unchanged". The PR-12 fields (runMaxParallel / scaleUpCron /
  // scaleDownCron / scheduledScaleTo) are therefore always null here — this
  // editor never writes them.
  //
  // The backend judges family/mode coherence against the EFFECTIVE value
  // (supplied else stored), so a family switch MUST carry `runFamily` in the
  // same input as its family-specific field — otherwise e.g. setting `replicas`
  // while the row is still Task is rejected as "service-only".
  function buildInput(): AgentRunSpecInput {
    const base: AgentRunSpecInput = {
      runFamily: null,
      runMode: null,
      runCronExpression: null,
      runPaused: paused,
      replicas: null,
      // PR-12 — never written by the Once/Schedule/Service editor.
      runMaxParallel: null,
      scheduledScaleTo: null,
      scaleUpCron: null,
      scaleDownCron: null,
    };
    if (family === "SERVICE") {
      return { ...base, runFamily: "SERVICE", replicas };
    }
    // Task family. Carry the mode + (for Schedule only) the cron — the backend
    // ignores the cron otherwise, and requires it for Schedule. Switching back
    // from Service carries runFamily so the effective family flips to TASK in
    // the same write.
    return {
      ...base,
      runFamily: "TASK",
      runMode: mode,
      runCronExpression: mode === "SCHEDULE" ? cron.trim() : null,
    };
  }

  async function handleSave() {
    if (!canSave) return;
    setFieldError(null);
    try {
      const { data } = await save({ variables: { agentSlug: agent.slug, input: buildInput() } });
      const result = data?.updateAgentRunSpec;
      if (result?.ok) {
        const persisted = result.data;
        // Re-seed from the persisted spec — notably `replicas`, the field the
        // list row can't provide. After this the Service editor reflects the
        // real stored baseline.
        if (persisted) {
          setReplicas(persisted.replicas);
          setReplicasSeeded(true);
        }
        toast.success(`Run spec saved for ${agent.name}`);
      } else {
        const err = result?.errors?.[0];
        if (err?.field) {
          setFieldError({ field: err.field, message: err.message });
        }
        toast.error(`Couldn't save run spec`, {
          description: err?.message ?? "Unknown error",
        });
      }
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      toast.error(`Couldn't save run spec for ${agent.name}`, { description: message });
    }
  }

  return (
    <div className="space-y-6">
      {/* Family selector — Task vs Service (spec §4: the Job-vs-Deployment
          split). Switching family resets the mode to the family's default so
          the editor never shows a Task mode under Service. */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Run family</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-muted-foreground text-sm">
            How this agent runs. A <span className="font-medium">Task</span> runs to completion as a
            Kubernetes Job; a <span className="font-medium">Service</span> stays running as a
            Deployment with N replicas.
          </p>
          <div className="grid gap-2 md:grid-cols-2">
            <OptionCard
              selected={family === "TASK"}
              onClick={() => {
                clearError();
                setFamily("TASK");
                // Default Task mode is Once unless a real Task mode is already
                // chosen (Schedule). Never land on a PR-12 mode by default.
                setMode((m) => (m === "SCHEDULE" ? "SCHEDULE" : "ONCE"));
              }}
              icon={PlayIcon}
              label="Task"
              description="Ephemeral — runs to completion, then exits (k8s Job)."
            />
            <OptionCard
              selected={family === "SERVICE"}
              onClick={() => {
                clearError();
                setFamily("SERVICE");
              }}
              icon={ServerIcon}
              label="Service"
              description="Always-on — stays running with N replicas (k8s Deployment)."
            />
          </div>
        </CardContent>
      </Card>

      {/* Task modes — Once / Schedule (real) + Loop / Trigger (PR-12, disabled). */}
      {family === "TASK" && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Task mode</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-muted-foreground text-sm">
              What causes this agent to dispatch a run?
            </p>
            <div className="grid gap-2 md:grid-cols-2">
              {TASK_MODES.map((opt) => (
                <OptionCard
                  key={opt.value}
                  selected={mode === opt.value}
                  disabled={opt.comingSoon}
                  onClick={() => {
                    clearError();
                    setMode(opt.value);
                  }}
                  icon={opt.icon}
                  label={opt.label}
                  description={opt.description}
                  badge={opt.comingSoon ? "PR-12" : undefined}
                />
              ))}
            </div>

            {/* Schedule config — the cloned cron editor. */}
            {mode === "SCHEDULE" && (
              <section className="space-y-2 rounded-md border p-4">
                <Label htmlFor="run-cron">Cron expression</Label>
                <input
                  id="run-cron"
                  type="text"
                  value={cron}
                  onChange={(e) => {
                    clearError();
                    setCron(e.target.value);
                  }}
                  placeholder="0 */6 * * *"
                  className={cn(
                    "border-input bg-background w-full rounded-md border px-2 py-2 font-mono text-sm",
                    (!cronValid && cron.trim().length > 0) ||
                      fieldError?.field === "runCronExpression"
                      ? "border-destructive"
                      : undefined
                  )}
                  aria-invalid={
                    (!cronValid && cron.trim().length > 0) ||
                    fieldError?.field === "runCronExpression"
                  }
                />
                <p className="text-muted-foreground text-xs">
                  Five fields (minute hour day month weekday). {naturalCronHint(cron)}
                </p>
                {fieldError?.field === "runCronExpression" && (
                  <p className="text-destructive text-xs">{fieldError.message}</p>
                )}
              </section>
            )}

            {/* Loop / Trigger explainer when one is selected. */}
            {isComingSoonMode && (
              <div className="bg-muted/30 text-muted-foreground flex items-start gap-2 rounded-md border border-dashed p-4 text-xs">
                <InfoIcon className="mt-0.5 size-4 shrink-0" />
                <span>
                  {mode === "LOOP" ? "Loop" : "Trigger"} configuration (
                  {mode === "LOOP" ? "the concurrency cap" : "the webhook/event binding"}) lands in
                  PR-12. This mode can&apos;t be saved here yet — pick Once or Schedule to save.
                </span>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Service config — the replica baseline (reused scaling-card control). */}
      {family === "SERVICE" && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Replicas</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-muted-foreground text-sm">
              The baseline number of replicas the agent Deployment runs. The per-environment maximum
              is applied when the Deployment is scaled.
            </p>
            {!replicasSeeded && (
              <div className="bg-muted/30 text-muted-foreground flex items-start gap-2 rounded-md border border-dashed p-3 text-xs">
                <InfoIcon className="mt-0.5 size-4 shrink-0" />
                <span>
                  Showing the default ({REPLICA_MIN}). The current stored replica count isn&apos;t
                  available to read here yet — save to set it explicitly, and it will read back the
                  persisted value.
                </span>
              </div>
            )}
            <div className="flex items-center gap-3">
              <span className="text-muted-foreground font-mono text-xs tabular-nums">
                {REPLICA_MIN}
              </span>
              <input
                type="range"
                min={REPLICA_MIN}
                max={REPLICA_MAX}
                step={1}
                value={replicas}
                onChange={(e) => {
                  clearError();
                  setReplicas(Number(e.target.value));
                }}
                className={cn(
                  "h-2 w-full cursor-pointer appearance-none rounded-full",
                  "bg-muted",
                  "[&::-webkit-slider-thumb]:appearance-none",
                  "[&::-webkit-slider-thumb]:size-4",
                  "[&::-webkit-slider-thumb]:rounded-full",
                  "[&::-webkit-slider-thumb]:bg-primary",
                  "[&::-moz-range-thumb]:size-4",
                  "[&::-moz-range-thumb]:rounded-full",
                  "[&::-moz-range-thumb]:border-0",
                  "[&::-moz-range-thumb]:bg-primary"
                )}
                aria-label="Replicas"
              />
              <span className="text-muted-foreground font-mono text-xs tabular-nums">
                {REPLICA_MAX}
              </span>
              <span className="min-w-[3rem] text-right font-mono text-lg tabular-nums">
                {replicas}
              </span>
            </div>
            {fieldError?.field === "replicas" && (
              <p className="text-destructive text-xs">{fieldError.message}</p>
            )}
            <div className="bg-muted/20 text-muted-foreground rounded-md border border-dashed p-3 text-xs">
              Scheduled scaling (scale to X on one cron, to 0 on another) is configured in PR-12.
            </div>
          </CardContent>
        </Card>
      )}

      {/* Pause toggle — owned by every mode (Once/Schedule/Service). Halts
          scheduled dispatch / holds the run-spec without losing its config. */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Pause</CardTitle>
        </CardHeader>
        <CardContent>
          <label className="flex items-start gap-3 text-sm">
            <input
              type="checkbox"
              className="mt-0.5 size-4"
              checked={paused}
              onChange={(e) => {
                clearError();
                setPaused(e.target.checked);
              }}
            />
            <span>
              <span className="font-medium">Pause this agent</span>
              <span className="text-muted-foreground mt-1 block text-xs">
                Holds the run spec without discarding it. Scheduled dispatches and the Service
                Deployment are suspended until you resume.
              </span>
            </span>
          </label>
        </CardContent>
      </Card>

      {/* A non-field server error (e.g. agentSlug / permission) that didn't map
          to a specific control. */}
      {fieldError &&
        fieldError.field !== "runCronExpression" &&
        fieldError.field !== "replicas" && (
          <div className="text-destructive flex items-center gap-2 text-sm">
            <InfoIcon className="size-4" />
            {fieldError.message}
          </div>
        )}

      <div className="flex items-center justify-end gap-2">
        <Button onClick={handleSave} disabled={!canSave}>
          {saving ? (
            <Loader2Icon className="size-4 animate-spin" />
          ) : (
            <SaveIcon className="size-4" />
          )}
          Save run spec
        </Button>
      </div>
    </div>
  );
}

/**
 * Card-style selector, cloned from the inline `OptionCard` in
 * `apps/new/steps/DeployStrategyStep.tsx` (it is not a shared export there).
 * Extended with a `disabled` state for the PR-12 "coming soon" modes so they
 * render visibly-inert rather than being omitted from the model.
 */
function OptionCard({
  selected,
  onClick,
  icon: Icon,
  label,
  description,
  badge,
  disabled,
}: {
  selected: boolean;
  onClick: () => void;
  icon: typeof PlayIcon;
  label: string;
  description: string;
  badge?: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-pressed={selected}
      className={cn(
        "flex flex-col items-start gap-2 rounded-md border p-4 text-left transition-colors",
        selected
          ? "border-primary bg-primary/5 ring-primary/20 ring-2"
          : "hover:bg-muted/50 border-border",
        disabled && "cursor-not-allowed opacity-60 hover:bg-transparent"
      )}
    >
      <div className="flex w-full items-center justify-between">
        <Icon className={cn("size-5", selected ? "text-primary" : "text-muted-foreground")} />
        {badge && (
          <Badge variant="outline" className="text-[10px]">
            {badge}
          </Badge>
        )}
      </div>
      <div>
        <p className="font-medium">{label}</p>
        <p className="text-muted-foreground mt-1 text-xs leading-relaxed">{description}</p>
      </div>
    </button>
  );
}
