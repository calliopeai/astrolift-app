"use client";

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

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import type {
  AgentRunFamily,
  AgentRunMode,
  AgentRunSpecInput,
} from "@/graphql/agents/agents.types";
import { cn } from "@/lib/utils";

import type { useAgentControl } from "./use-agent-control";

// ---------------------------------------------------------------------------
// Run-spec editor — Once / Schedule / Loop / Service (+ scheduled scaling)
// (spec 33 PR-11 → extended by PR-12)
//
// PR-11 filled the PR-9 Control-tab stub with the editor for the three modes
// whose backend landed in PR-1/PR-4 (Once / Schedule / Service). PR-12 extends
// that same editor with the two run-spec features whose write mutations are now
// deployed:
//   - LOOP (Task family) — concurrency-capped continuous re-dispatch, written
//     via `runMode=LOOP` + `runMaxParallel` (PR-6 backend);
//   - Service SCHEDULED SCALING — scale-to-X on an up-cron / scale-to-0 on a
//     down-cron, written via `scaleUpCron` + `scheduledScaleTo` + `scaleDownCron`
//     (PR-5 backend).
//
// Trigger is now ENABLED (spec 33 PR-6/PR-12; #951): the bind/list/unbind
// surface shipped (createAgentTrigger / unbindAgentTrigger / agentTriggers) and
// payload threading landed (#930). Selecting Trigger writes runMode=TRIGGER
// (+ runPaused) via the same Save below AND renders the TriggerBindingEditor for
// the webhook binding (bind / list / unbind, with a one-time signing-secret
// reveal). No mode card stays disabled anymore.
//
// Reuse (per the spec's reuse-heavy mandate):
//   - the cron text input + `naturalCronHint` read-back + `CRON_FIELD` 5-field
//     validation, cloned by PR-11 from `apps/new/steps/DeployStrategyStep.tsx`.
//     PR-12 REUSES PR-11's copies in this file (the `CronField` extraction
//     below) for the two scaling crons — it does NOT re-clone the source;
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

/**
 * The cron text input + `naturalCronHint` read-back + invalid-state styling,
 * extracted verbatim from PR-11's inline Schedule editor so the two PR-12
 * scaling crons reuse the SAME control instead of re-cloning DeployStrategyStep.
 * Identical markup/validation to what PR-11 rendered inline for `run-cron`;
 * lifted to a component now that three crons share it (run-schedule, scale-up,
 * scale-down).
 *
 * `invalid` lets the caller fold in a mapped server field error on top of the
 * local 5-field check (PR-11 styled the input on either condition).
 */
function CronField({
  id,
  label,
  value,
  placeholder,
  invalid,
  errorMessage,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  placeholder: string;
  invalid: boolean;
  errorMessage?: string;
  onChange: (next: string) => void;
}) {
  return (
    <div className="space-y-2">
      <Label htmlFor={id}>{label}</Label>
      <input
        id={id}
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className={cn(
          "border-input bg-background w-full rounded-md border px-2 py-2 font-mono text-sm",
          invalid ? "border-destructive" : undefined
        )}
        aria-invalid={invalid}
      />
      <p className="text-muted-foreground text-xs">
        Five fields (minute hour day month weekday). {naturalCronHint(value)}
      </p>
      {errorMessage && <p className="text-destructive text-xs">{errorMessage}</p>}
    </div>
  );
}

// Service replica bounds. The run-spec `replicas` is the declared baseline the
// Deployment renderer reads; the env-max clamp is resolved live at deploy/scale
// time server-side (the mutation only enforces >= 1). We bound the slider at a
// sane editor ceiling so the control stays usable; an out-of-policy value is
// caught by the backend and surfaced as a field error.
const REPLICA_MIN = 1;
const REPLICA_MAX = 20;

// Loop concurrency cap (`runMaxParallel`). The backend contract (PR-10b):
// task-only, integer >= 0. Semantics surfaced in the UI:
//   - 0   → soft-pause (the loop holds; no new runs dispatched);
//   - 1 / empty → serial (one run at a time — the PR-6 default when unset);
//   - N>1 → up to N concurrent runs before re-dispatch waits.
// The number input keeps an empty string distinct from "0" (empty = leave at
// the backend default = serial), so we don't conflate "unset" with "paused".
const MAX_PARALLEL_MIN = 0;

// `errors[].field` values the editor renders INLINE beside their control. A
// server error on any of these is shown under the matching input; anything else
// falls through to the catch-all banner near the Save button. Kept here so the
// inline render sites and the catch-all exclusion can't drift apart.
const MAPPED_ERROR_FIELDS = new Set<string>([
  "runCronExpression",
  "replicas",
  "runMaxParallel",
  "scaleUpCron",
  "scaleDownCron",
  "scheduledScaleTo",
]);

// The two families and the Task modes, in spec §4 order. All four Task modes are
// now writable: PR-12 enabled Loop (its `runMaxParallel` write landed in PR-6),
// and #951 enabled Trigger (createAgentTrigger + payload threading via #930), so
// none stay disabled — selecting any writes its run-spec via the Save below.
type ModeOption = {
  value: AgentRunMode;
  label: string;
  description: string;
  icon: typeof PlayIcon;
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
  },
  {
    value: "TRIGGER",
    label: "Trigger",
    description: "Dispatches when a bound webhook or event fires.",
    icon: WebhookIcon,
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

export type AgentControlScreenProps = Omit<ReturnType<typeof useAgentControl>, "orgId"> & {
  /** The Trigger-mode webhook binding editor, rendered when Trigger is selected. */
  triggerEditor: React.ReactNode;
};

/**
 * Control tab content for an agent (spec 33 PR-11, extended by PR-12) — the
 * run-spec editor.
 *
 * Seeds every run setting from the resolved fleet row. Polls of the same
 * agent preserve edits; changing agent identity reads the new row. A successful
 * save reads the persisted values returned by the mutation.
 */
export function AgentControlScreen({
  agent,
  saving,
  onSave,
  triggerEditor,
}: AgentControlScreenProps) {
  // --- Editor state, seeded from the resolved (lowercase) read row. --------
  const [family, setFamily] = React.useState<AgentRunFamily>(() => toFamily(agent.runFamily));
  const [mode, setMode] = React.useState<AgentRunMode>(() => toMode(agent.runMode));
  const [cron, setCron] = React.useState<string>(agent.runCronExpression ?? "");
  const [paused, setPaused] = React.useState<boolean>(Boolean(agent.runPaused));
  const [replicas, setReplicas] = React.useState(agent.replicas);
  // Keep null distinct from a stored zero (the Loop soft-pause value).
  const [maxParallel, setMaxParallel] = React.useState(
    agent.runMaxParallel == null ? "" : String(agent.runMaxParallel)
  );
  const [scalingEnabled, setScalingEnabled] = React.useState(
    Boolean(agent.scaleUpCron || agent.scaleDownCron || agent.scheduledScaleTo != null)
  );
  const [scaleUpCron, setScaleUpCron] = React.useState(agent.scaleUpCron);
  const [scaleDownCron, setScaleDownCron] = React.useState(agent.scaleDownCron);
  const [scaleTo, setScaleTo] = React.useState(
    agent.scheduledScaleTo == null ? "" : String(agent.scheduledScaleTo)
  );

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
    setReplicas(agent.replicas);
    setMaxParallel(agent.runMaxParallel == null ? "" : String(agent.runMaxParallel));
    setScalingEnabled(
      Boolean(agent.scaleUpCron || agent.scaleDownCron || agent.scheduledScaleTo != null)
    );
    setScaleUpCron(agent.scaleUpCron);
    setScaleDownCron(agent.scaleDownCron);
    setScaleTo(agent.scheduledScaleTo == null ? "" : String(agent.scheduledScaleTo));
    setFieldError(null);
  }

  // Loop concurrency cap validity: empty is valid (= leave at the backend
  // serial default). A supplied value must be a non-negative integer (0 =
  // soft-pause). `Number()` of a blank string is 0, so guard on length first.
  const maxParallelTrimmed = maxParallel.trim();
  const maxParallelValid =
    maxParallelTrimmed.length === 0 ||
    (/^\d+$/.test(maxParallelTrimmed) && Number(maxParallelTrimmed) >= MAX_PARALLEL_MIN);

  // Scheduled-scaling validity — only enforced when the optional section is on.
  // Both crons must be 5-field; the scale-UP target must be a positive integer
  // (scaling "up" to 0 is meaningless — the down-cron is what scales to 0).
  const scaleToTrimmed = scaleTo.trim();
  const scaleUpCronValid = CRON_FIELD.test(scaleUpCron.trim());
  const scaleDownCronValid = CRON_FIELD.test(scaleDownCron.trim());
  const scaleToValid = /^\d+$/.test(scaleToTrimmed) && Number(scaleToTrimmed) >= 1;
  const scalingValid = !scalingEnabled || (scaleUpCronValid && scaleDownCronValid && scaleToValid);

  // Client-side validity (immediate feedback; the backend remains the source of
  // truth and re-checks all of this):
  //   - Task + Schedule  → cron must be a 5-field expression
  //   - Task + Loop      → cap empty or a non-negative integer
  //   - Service          → replicas in [REPLICA_MIN, REPLICA_MAX]; if scheduled
  //                        scaling is on, both crons valid + target >= 1
  //   - Trigger          → no run-spec field to validate (Save writes only the
  //                        mode + paused; the webhook binding is written
  //                        separately by the TriggerBindingEditor), so canSave
  //                        falls through to the `true` default
  const cronValid = CRON_FIELD.test(cron.trim());
  const replicasValid = replicas >= REPLICA_MIN && replicas <= REPLICA_MAX;
  const canSave =
    !saving &&
    (family === "SERVICE"
      ? replicasValid && scalingValid
      : mode === "SCHEDULE"
        ? cronValid
        : mode === "LOOP"
          ? maxParallelValid
          : true);

  function clearError() {
    if (fieldError) setFieldError(null);
  }

  // Build the input the editor owns for the chosen family/mode. Every key is
  // present (the generated input has no optionals — `avoidOptionals`), so an
  // unowned field is sent as `null`, which the partial-update backend reads as
  // "leave unchanged".
  //
  // Coherence (PR-10b contract): the loop cap is task-only and the scaling
  // fields are service-only, so we only ever carry one family's PR-12 fields —
  // matching the family we send in the same input. Trigger carries only its mode
  // here (its webhook binding is written separately by the TriggerBindingEditor,
  // not through this run-spec input).
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
      runMaxParallel: null,
      scheduledScaleTo: null,
      scaleUpCron: null,
      scaleDownCron: null,
      // Explicit "clear the scheduled-scaling triple" flag (#952); default
      // false = leave scheduled scaling untouched. The scaling setters below
      // never combine with it (backend rejects both at once).
      clearScheduledScaling: false,
    };
    if (family === "SERVICE") {
      // Service: the replica baseline, plus the optional scheduled-scaling
      // triple. When scaling is OFF we leave the three fields null
      // ("unchanged") rather than clearing them — disabling the section in the
      // UI is not the same as an explicit "turn scaling off" write (there is no
      // such single signal in the contract; an operator clears it by blanking
      // the crons, which the backend validates).
      if (scalingEnabled) {
        return {
          ...base,
          runFamily: "SERVICE",
          replicas,
          scaleUpCron: scaleUpCron.trim(),
          scaleDownCron: scaleDownCron.trim(),
          scheduledScaleTo: Number(scaleTo.trim()),
        };
      }
      return { ...base, runFamily: "SERVICE", replicas };
    }
    // Task family. Carry the mode + the mode's owned field: the cron for
    // Schedule, the concurrency cap for Loop. Once and Trigger have no owned
    // run-spec field here (Trigger's binding is a separate mutation), so they
    // send just the mode. The backend ignores the cron otherwise and requires it
    // for Schedule; the cap is task-only. An empty cap stays null (leave at the
    // backend serial default); "0" is sent as 0 (soft-pause). Switching back from
    // Service carries runFamily so the effective family flips to TASK in the same
    // write.
    return {
      ...base,
      runFamily: "TASK",
      runMode: mode,
      runCronExpression: mode === "SCHEDULE" ? cron.trim() : null,
      runMaxParallel:
        mode === "LOOP" && maxParallel.trim().length > 0 ? Number(maxParallel.trim()) : null,
    };
  }

  async function handleSave() {
    if (!canSave) return;
    setFieldError(null);
    const result = await onSave(buildInput());
    if (result.ok) {
      const persisted = result.persisted;
      // Re-seed from the persisted spec — the fields the list row can't
      // provide (`replicas` + the PR-12 fields). After this every control
      // reflects the real stored value.
      if (persisted) {
        setReplicas(persisted.replicas);
        // Loop cap: null read-back → blank (= serial default); 0 → "0"
        // (soft-pause), shown verbatim.
        setMaxParallel(persisted.runMaxParallel == null ? "" : String(persisted.runMaxParallel));
        // Scaling crons are non-null strings ("" when unset). Scheduled
        // scaling counts as configured when either cron is set.
        const upCron = persisted.scaleUpCron ?? "";
        const downCron = persisted.scaleDownCron ?? "";
        setScaleUpCron(upCron);
        setScaleDownCron(downCron);
        setScaleTo(persisted.scheduledScaleTo == null ? "" : String(persisted.scheduledScaleTo));
        setScalingEnabled(upCron.trim().length > 0 || downCron.trim().length > 0);
      }
    } else if (result.fieldError) {
      setFieldError(result.fieldError);
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
                // Default Task mode is Once unless a real, writable Task mode is
                // already chosen (Schedule or Loop). Never land on the
                // unwritable Trigger mode by default.
                setMode((m) => (m === "SCHEDULE" || m === "LOOP" ? m : "ONCE"));
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

      {/* Task modes — Once / Schedule / Loop / Trigger (all writable). */}
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
                  onClick={() => {
                    clearError();
                    setMode(opt.value);
                  }}
                  icon={opt.icon}
                  label={opt.label}
                  description={opt.description}
                />
              ))}
            </div>

            {/* Schedule config — the shared cron editor (CronField). */}
            {mode === "SCHEDULE" && (
              <section className="rounded-md border p-4">
                <CronField
                  id="run-cron"
                  label="Cron expression"
                  value={cron}
                  placeholder="0 */6 * * *"
                  invalid={
                    (!cronValid && cron.trim().length > 0) ||
                    fieldError?.field === "runCronExpression"
                  }
                  errorMessage={
                    fieldError?.field === "runCronExpression" ? fieldError.message : undefined
                  }
                  onChange={(next) => {
                    clearError();
                    setCron(next);
                  }}
                />
              </section>
            )}

            {/* Loop config (PR-12) — the concurrency cap (`runMaxParallel`). */}
            {mode === "LOOP" && (
              <section className="space-y-2 rounded-md border p-4">
                <Label htmlFor="run-max-parallel">Concurrency cap</Label>
                <input
                  id="run-max-parallel"
                  type="number"
                  inputMode="numeric"
                  min={MAX_PARALLEL_MIN}
                  step={1}
                  value={maxParallel}
                  onChange={(e) => {
                    clearError();
                    setMaxParallel(e.target.value);
                  }}
                  placeholder="1"
                  className={cn(
                    "border-input bg-background w-28 rounded-md border px-2 py-2 font-mono text-sm",
                    (!maxParallelValid && maxParallelTrimmed.length > 0) ||
                      fieldError?.field === "runMaxParallel"
                      ? "border-destructive"
                      : undefined
                  )}
                  aria-invalid={
                    (!maxParallelValid && maxParallelTrimmed.length > 0) ||
                    fieldError?.field === "runMaxParallel"
                  }
                />
                <p className="text-muted-foreground text-xs">
                  How many runs may be in flight at once before the loop waits to re-dispatch.{" "}
                  <span className="font-medium">Leave blank</span> or set{" "}
                  <span className="font-mono">1</span> for serial (one run at a time).{" "}
                  <span className="font-mono">0</span> soft-pauses the loop (no new runs) without
                  changing the mode.
                </p>
                {fieldError?.field === "runMaxParallel" && (
                  <p className="text-destructive text-xs">{fieldError.message}</p>
                )}
              </section>
            )}

            {/* Trigger config (#951) — the webhook binding editor. Save (below)
                still writes runMode=TRIGGER + runPaused; this section manages the
                bindings themselves (bind / list / unbind). */}
            {mode === "TRIGGER" && triggerEditor}
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

            {/* Scheduled scaling (PR-12) — optional. A service runs flat at the
                baseline above unless this is turned on; when on, it scales UP
                to a target on one cron and DOWN to 0 on another. */}
            <div className="space-y-3 rounded-md border p-4">
              <label className="flex items-start gap-3 text-sm">
                <input
                  type="checkbox"
                  className="mt-0.5 size-4"
                  checked={scalingEnabled}
                  onChange={(e) => {
                    clearError();
                    setScalingEnabled(e.target.checked);
                  }}
                />
                <span>
                  <span className="font-medium">Scheduled scaling</span>
                  <span className="text-muted-foreground mt-1 block text-xs">
                    Optional. Scale the Deployment up to a target replica count on one cron and back
                    down to 0 on another (for example, run during business hours only). Leave off to
                    run flat at the baseline replica count above.
                  </span>
                </span>
              </label>

              {scalingEnabled && (
                <div className="space-y-4 border-t pt-3">
                  {/* Scale UP — cron + target replica count X. */}
                  <div className="space-y-2">
                    <CronField
                      id="scale-up-cron"
                      label="Scale-up cron"
                      value={scaleUpCron}
                      placeholder="0 8 * * 1-5"
                      invalid={
                        (!scaleUpCronValid && scaleUpCron.trim().length > 0) ||
                        fieldError?.field === "scaleUpCron"
                      }
                      errorMessage={
                        fieldError?.field === "scaleUpCron" ? fieldError.message : undefined
                      }
                      onChange={(next) => {
                        clearError();
                        setScaleUpCron(next);
                      }}
                    />
                    <Label htmlFor="scale-to">Scale up to (replicas)</Label>
                    <input
                      id="scale-to"
                      type="number"
                      inputMode="numeric"
                      min={1}
                      step={1}
                      value={scaleTo}
                      onChange={(e) => {
                        clearError();
                        setScaleTo(e.target.value);
                      }}
                      placeholder="3"
                      className={cn(
                        "border-input bg-background w-28 rounded-md border px-2 py-2 font-mono text-sm",
                        (!scaleToValid && scaleToTrimmed.length > 0) ||
                          fieldError?.field === "scheduledScaleTo"
                          ? "border-destructive"
                          : undefined
                      )}
                      aria-invalid={
                        (!scaleToValid && scaleToTrimmed.length > 0) ||
                        fieldError?.field === "scheduledScaleTo"
                      }
                    />
                    <p className="text-muted-foreground text-xs">
                      The replica count the Deployment scales to when the scale-up cron fires (must
                      be at least 1).
                    </p>
                    {fieldError?.field === "scheduledScaleTo" && (
                      <p className="text-destructive text-xs">{fieldError.message}</p>
                    )}
                  </div>

                  {/* Scale DOWN — cron (always scales to 0). */}
                  <div className="space-y-2">
                    <CronField
                      id="scale-down-cron"
                      label="Scale-down cron"
                      value={scaleDownCron}
                      placeholder="0 18 * * 1-5"
                      invalid={
                        (!scaleDownCronValid && scaleDownCron.trim().length > 0) ||
                        fieldError?.field === "scaleDownCron"
                      }
                      errorMessage={
                        fieldError?.field === "scaleDownCron" ? fieldError.message : undefined
                      }
                      onChange={(next) => {
                        clearError();
                        setScaleDownCron(next);
                      }}
                    />
                    <p className="text-muted-foreground text-xs">
                      When this cron fires the Deployment scales{" "}
                      <span className="font-medium">to 0</span> (fully stopped) until the next
                      scale-up.
                    </p>
                  </div>
                </div>
              )}
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
          to a specific control — i.e. its `field` isn't one rendered inline
          beside a control above. */}
      {fieldError && !MAPPED_ERROR_FIELDS.has(fieldError.field) && (
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
 * Backs both the family selector (Task / Service) and the Task-mode selector.
 */
function OptionCard({
  selected,
  onClick,
  icon: Icon,
  label,
  description,
}: {
  selected: boolean;
  onClick: () => void;
  icon: typeof PlayIcon;
  label: string;
  description: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      className={cn(
        "flex flex-col items-start gap-2 rounded-md border p-4 text-left transition-colors",
        selected
          ? "border-primary bg-primary/5 ring-primary/20 ring-2"
          : "hover:bg-muted/50 border-border"
      )}
    >
      <Icon className={cn("size-5", selected ? "text-primary" : "text-muted-foreground")} />
      <div>
        <p className="font-medium">{label}</p>
        <p className="text-muted-foreground mt-1 text-xs leading-relaxed">{description}</p>
      </div>
    </button>
  );
}
