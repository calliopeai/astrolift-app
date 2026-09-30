"use client";

import { useTranslations } from "next-intl";
import type { ManifestErr, FieldSpec } from "@/lib/manifest/schema";

type Translator = ReturnType<typeof useTranslations<"apps.config">>;
const fixedErrors: Record<string, string> = {
  "Top-level app name is required.": "appName",
  "Workload name is required.": "workloadName",
  "Cronjob workloads require a schedule.": "schedule",
  "Workflow workloads require a workflow type.": "workflowType",
  "Workflow workloads require a task queue.": "taskQueue",
  "A build command requires an output dir.": "outputDir",
  "image-package FaaS must not set handler/runtime.": "imageHandler",
  "Container name is required.": "containerName",
  "Managed service kind is required.": "serviceKind",
  "astrolift_version is required for an agent config.": "agentVersion",
  "Skill slug is required.": "skillSlug",
  "Tool slug is required.": "toolSlug",
};
const patterns: { pattern: RegExp; key: string; argument: string }[] = [
  {
    pattern: /^Duplicate workload name "([\s\S]*)"\.$/,
    key: "duplicateWorkload",
    argument: "name",
  },
  { pattern: /^Kind must be one of ([\s\S]*)\.$/, key: "kind", argument: "options" },
  {
    pattern: /^Concurrency policy must be one of ([\s\S]*)\.$/,
    key: "concurrencyPolicy",
    argument: "options",
  },
  { pattern: /^zip-package FaaS requires ([\s\S]*)\.$/, key: "zipRequired", argument: "key" },
  {
    pattern: /^([\s\S]*) workloads must declare no containers\.$/,
    key: "noContainers",
    argument: "kind",
  },
  {
    pattern: /^Healthcheck kind must be one of ([\s\S]*)\.$/,
    key: "healthcheck",
    argument: "options",
  },
  { pattern: /^Duplicate skill slug "([\s\S]*)"\.$/, key: "duplicateSkill", argument: "slug" },
  { pattern: /^Duplicate tool slug "([\s\S]*)"\.$/, key: "duplicateTool", argument: "slug" },
  {
    pattern: /^"([\s\S]*)" is a reserved key — set it in the field above\.$/,
    key: "reserved",
    argument: "key",
  },
];
const jsonFieldLabels: Record<string, string> = {
  input_schema: "builder.inputSchema",
  output_schema: "builder.outputSchema",
  implementation_config: "builder.implementationConfig",
};

/** Translate known client-validator prose, retaining paths and parser diagnostics. */
export function localizeConfigError(error: ManifestErr, t: Translator): ManifestErr {
  if (Object.hasOwn(fixedErrors, error.message))
    return { ...error, message: t(`validation.${fixedErrors[error.message]}`) };
  for (const { pattern, key, argument } of patterns) {
    const match = pattern.exec(error.message);
    if (match) return { ...error, message: t(`validation.${key}`, { [argument]: match[1] }) };
  }
  const fieldKey = error.path.split(".").at(-1) ?? "";
  if (Object.hasOwn(jsonFieldLabels, fieldKey)) {
    const field = t(jsonFieldLabels[fieldKey]);
    const reason = error.message.replace(/^[^:]*: /, "");
    if (reason === "must be a JSON object")
      return { ...error, message: t("validation.jsonObject", { field }) };
    if (
      reason === "value is not representable in TOML (e.g. null)" ||
      reason === "not representable in TOML"
    )
      return { ...error, message: t("validation.notRepresentable", { field }) };
    return { ...error, message: t("validation.jsonDiagnostic", { field, reason }) };
  }
  return error;
}

export function useConfigCopy() {
  const t = useTranslations("apps.config");
  return {
    fieldLabel: (spec: FieldSpec) =>
      t.has(`fields.${spec.key}`) ? t(`fields.${spec.key}`) : spec.label,
    fieldHelp: (spec: FieldSpec) =>
      spec.help === "Required for workflow workers." ? t("fields.workflowRequired") : spec.help,
    errors: (errors: ManifestErr[]) => errors.map((error) => localizeConfigError(error, t)),
    reason: (reason: string | undefined) =>
      reason === "the manifest uses TOML this editor can't round-trip"
        ? t("validation.unsafeRoundTrip")
        : (reason ?? ""),
  };
}
