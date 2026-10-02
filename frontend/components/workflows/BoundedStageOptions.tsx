"use client";

import { useId } from "react";
import { useTranslations } from "next-intl";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import { isScalar, readBackEdge, type WorkflowBackEdge } from "./back-edge";

export interface BoundedStageOptionsProps {
  kind: string;
  maxAttempts: number;
  backEdge: unknown;
  valueJson?: string;
  targets: string[];
  disabled: boolean;
  onChange: (patch: {
    maxAttempts?: number;
    backEdge?: unknown;
    backEdgeValueJson?: string;
  }) => void;
}

/** Explicit prior output keys, independently bounded attempts and review rounds. */
export function BoundedStageOptions({
  kind,
  maxAttempts,
  backEdge,
  valueJson,
  targets,
  disabled,
  onChange,
}: BoundedStageOptionsProps) {
  const t = useTranslations("workflowBounds");
  const id = useId();
  const valid = readBackEdge(backEdge);
  const raw = backEdge as Partial<WorkflowBackEdge> | null;
  const edge =
    valid ??
    (raw && typeof raw.max_rounds === "number" && readBackEdge({ ...raw, max_rounds: 1 })
      ? (raw as WorkflowBackEdge)
      : null);
  const imported = edge?.source_format === "flowise_loop_1_2";
  const rawPresent = Boolean(
    backEdge && typeof backEdge === "object" && Object.keys(backEdge).length
  );
  const json = valueJson ?? (edge && "value" in edge ? JSON.stringify(edge.value) : "false");
  let invalidValue = false;
  if (edge?.when === "output_equals") {
    try {
      invalidValue = !isScalar(JSON.parse(json));
    } catch {
      invalidValue = true;
    }
  }
  const patchEdge = (patch: Partial<WorkflowBackEdge>) => {
    if (edge) onChange({ backEdge: { ...edge, ...patch } });
  };
  const options: WorkflowBackEdge["when"][] = [
    ...(kind === "human_gate" ? ["gate_rejected" as const] : []),
    ...(["agent_dispatch", "workflow"].includes(kind) ? ["stage_failed" as const] : []),
    "output_equals",
    ...(edge?.when === "always" ? ["always" as const] : []),
  ];
  const control = "border-input bg-background h-8 w-full rounded-md border px-2 text-xs";
  return (
    <fieldset className="flex min-w-0 flex-col gap-3 rounded-md border p-3" disabled={disabled}>
      <legend className="px-1 text-xs font-medium">{t("title")}</legend>
      {["agent_dispatch", "workflow"].includes(kind) && (
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-attempts`} className="text-xs">
            {t("attempts")}
          </Label>
          <Input
            id={`${id}-attempts`}
            type="number"
            min={1}
            max={20}
            value={maxAttempts}
            className="h-8 text-xs"
            onChange={(e) => onChange({ maxAttempts: Number(e.target.value) })}
          />
          <p className="text-muted-foreground text-xs">{t("attemptHint")}</p>
        </div>
      )}
      <label className="flex items-center gap-2 text-xs" htmlFor={`${id}-enabled`}>
        <input
          id={`${id}-enabled`}
          type="checkbox"
          checked={rawPresent}
          disabled={disabled || (!rawPresent && targets.length === 0)}
          onChange={(e) =>
            onChange({
              backEdge: e.target.checked
                ? {
                    to: targets[0],
                    when: options[0],
                    max_rounds: 3,
                    on_exhausted: "fail",
                    ...(options[0] === "output_equals" ? { path: "again", value: true } : {}),
                  }
                : {},
              backEdgeValueJson: undefined,
            })
          }
        />
        {t("enable")}
      </label>
      {!rawPresent && targets.length === 0 && (
        <p className="text-muted-foreground text-xs">{t("noTargets")}</p>
      )}
      {rawPresent && !edge && (
        <p role="alert" className="text-danger-fg text-xs">
          {t("invalidEdge")}
        </p>
      )}
      {edge && (
        <>
          <div className="grid grid-cols-2 gap-3">
            <div className="flex min-w-0 flex-col gap-1.5">
              <Label htmlFor={`${id}-target`} className="text-xs">
                {t("target")}
              </Label>
              <select
                id={`${id}-target`}
                className={control}
                value={edge.to}
                disabled={imported}
                onChange={(e) => patchEdge({ to: e.target.value })}
              >
                {!targets.includes(edge.to) && <option value={edge.to}>{edge.to}</option>}
                {targets.map((target) => (
                  <option key={target} value={target}>
                    {target}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex min-w-0 flex-col gap-1.5">
              <Label htmlFor={`${id}-trigger`} className="text-xs">
                {t("trigger")}
              </Label>
              <select
                id={`${id}-trigger`}
                className={control}
                value={edge.when}
                disabled={imported}
                onChange={(e) => {
                  const when = e.target.value as WorkflowBackEdge["when"];
                  onChange({
                    backEdge: {
                      to: edge.to,
                      max_rounds: edge.max_rounds,
                      on_exhausted: edge.on_exhausted,
                      when,
                      ...(when === "output_equals" ? { path: "again", value: true } : {}),
                    },
                    backEdgeValueJson: undefined,
                  });
                }}
              >
                {options.map((option) => (
                  <option key={option} value={option}>
                    {t(option)}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex min-w-0 flex-col gap-1.5">
              <Label htmlFor={`${id}-rounds`} className="text-xs">
                {t("rounds")}
              </Label>
              <Input
                id={`${id}-rounds`}
                type="number"
                min={1}
                max={20}
                value={edge.max_rounds}
                aria-invalid={
                  !Number.isInteger(edge.max_rounds) || edge.max_rounds < 1 || edge.max_rounds > 20
                }
                className="h-8 text-xs"
                onChange={(e) => patchEdge({ max_rounds: Number(e.target.value) })}
              />
            </div>
            <div className="flex min-w-0 flex-col gap-1.5">
              <Label htmlFor={`${id}-exhausted`} className="text-xs">
                {t("exhausted")}
              </Label>
              <select
                id={`${id}-exhausted`}
                className={control}
                value={edge.on_exhausted}
                disabled={imported}
                onChange={(e) =>
                  patchEdge({ on_exhausted: e.target.value as WorkflowBackEdge["on_exhausted"] })
                }
              >
                <option value="fail">{t("fail")}</option>
                <option value="escalate">{t("escalate")}</option>
                {imported && <option value="continue">{t("continue")}</option>}
              </select>
            </div>
          </div>
          {edge.when === "output_equals" && (
            <div className="grid grid-cols-2 gap-3">
              <div className="flex min-w-0 flex-col gap-1.5">
                <Label htmlFor={`${id}-path`} className="text-xs">
                  {t("path")}
                </Label>
                <Input
                  id={`${id}-path`}
                  value={edge.path}
                  className="h-8 text-xs"
                  onChange={(e) => patchEdge({ path: e.target.value })}
                />
              </div>
              <div className="flex min-w-0 flex-col gap-1.5">
                <Label htmlFor={`${id}-value`} className="text-xs">
                  {t("value")}
                </Label>
                <Input
                  id={`${id}-value`}
                  value={json}
                  aria-invalid={invalidValue}
                  className="h-8 text-xs"
                  onChange={(e) => onChange({ backEdgeValueJson: e.target.value })}
                />
                {invalidValue && (
                  <p role="alert" className="text-danger-fg text-xs">
                    {t("invalidValue")}
                  </p>
                )}
              </div>
            </div>
          )}
          {imported && (
            <div className="text-muted-foreground flex min-w-0 flex-col gap-1 text-xs break-words">
              <p>{t("importedHint")}</p>
              <code>{edge.source_target}</code>
              {"fallback_message" in edge && (
                <p>
                  {t("importedFallback")}: {JSON.stringify(edge.fallback_message)}
                </p>
              )}
            </div>
          )}
          <p className="text-muted-foreground text-xs">{t("roundHint")}</p>
        </>
      )}
    </fieldset>
  );
}
