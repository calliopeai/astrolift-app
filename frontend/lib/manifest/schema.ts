/**
 * Declarative field schema + validation for the visual manifest builder (#1110).
 *
 * This is the faithful, form-facing description of the astrolift.toml contract
 * (sourced from astrolift_manifest/parser.py + types.py). The retired
 * public/manifest-schema.json placeholder predated the current
 * [[workloads]]/kind/containers shape, so this descriptor is the source of
 * truth for generation + validation.
 * Validation mirrors the backend parser's hard rules so the form flags what
 * `updateManifest` would reject before the round-trip.
 */

import type { ManifestModel } from "./model";
import { WORKLOAD_KINDS } from "./model";

export type FieldWidget = "text" | "number" | "toggle" | "select";

export interface FieldSpec {
  key: string;
  label: string;
  widget: FieldWidget;
  options?: readonly string[];
  placeholder?: string;
  help?: string;
}

export const HEALTHCHECK_KINDS = ["none", "http", "tcp", "exec"] as const;
export const CONCURRENCY_POLICIES = ["forbid", "queue", "replace"] as const;
export const RUN_FAMILIES = ["task", "service"] as const;
export const FAAS_PACKAGE_TYPES = ["image", "zip"] as const;
/** Common managed-service kinds — a hint list, the backend accepts any string. */
export const MANAGED_SERVICE_KINDS = [
  "postgres",
  "redis",
  "cache",
  "object_storage",
  "kafka",
  "mysql",
  "mongodb",
] as const;

export const WORKLOAD_KIND_OPTIONS = WORKLOAD_KINDS;

/** Static sites and FaaS have no pod, so no containers and no pod resources. */
export function supportsContainers(kind: string): boolean {
  return kind !== "static_site" && kind !== "faas";
}
export function supportsResources(kind: string): boolean {
  return kind !== "static_site" && kind !== "faas";
}

export const WORKLOAD_RESOURCE_FIELDS: readonly FieldSpec[] = [
  { key: "cpu_request", label: "CPU request", widget: "text", placeholder: "100m" },
  { key: "cpu_limit", label: "CPU limit", widget: "text", placeholder: "500m" },
  { key: "memory_request", label: "Memory request", widget: "text", placeholder: "128Mi" },
  { key: "memory_limit", label: "Memory limit", widget: "text", placeholder: "256Mi" },
  { key: "hpa_min", label: "HPA min replicas", widget: "number" },
  { key: "hpa_max", label: "HPA max replicas", widget: "number" },
  { key: "hpa_target_cpu_pct", label: "HPA target CPU %", widget: "number", placeholder: "80" },
  { key: "storage_class", label: "Storage class", widget: "text" },
  { key: "storage_size", label: "Storage size", widget: "text", placeholder: "10Gi" },
];

export const CONTAINER_FIELDS: readonly FieldSpec[] = [
  { key: "image_ref", label: "Image", widget: "text", placeholder: "ghcr.io/org/app:tag" },
  { key: "port", label: "Port", widget: "number", placeholder: "8080" },
  { key: "dockerfile_path", label: "Dockerfile path", widget: "text", placeholder: "Dockerfile" },
  { key: "build_context", label: "Build context", widget: "text", placeholder: "." },
];

/**
 * Per-kind tuning fields — these live in the workload's `extra` bag and are
 * only rendered for the matching kind. Keys match the backend TOML surface
 * (note function uses bare `concurrency` / `timeout_seconds`).
 */
export const KIND_TUNING: Record<string, readonly FieldSpec[]> = {
  agent: [
    { key: "run_family", label: "Run family", widget: "select", options: RUN_FAMILIES },
    { key: "max_retries", label: "Max retries", widget: "number", placeholder: "5" },
    {
      key: "tool_timeout_seconds",
      label: "Tool timeout (s)",
      widget: "number",
      placeholder: "300",
    },
    { key: "result_ttl_hours", label: "Result TTL (h)", widget: "number", placeholder: "72" },
  ],
  workflow: [
    {
      key: "workflow_type",
      label: "Workflow type",
      widget: "text",
      help: "Required for workflow workers.",
    },
    {
      key: "task_queue",
      label: "Task queue",
      widget: "text",
      help: "Required for workflow workers.",
    },
    {
      key: "temporal_namespace",
      label: "Temporal namespace",
      widget: "text",
      placeholder: "default",
    },
    {
      key: "max_concurrent_activities",
      label: "Max concurrent activities",
      widget: "number",
      placeholder: "20",
    },
    {
      key: "max_concurrent_workflows",
      label: "Max concurrent workflows",
      widget: "number",
      placeholder: "10",
    },
  ],
  function: [
    { key: "min_scale", label: "Min scale", widget: "number", placeholder: "0" },
    { key: "max_scale", label: "Max scale", widget: "number", placeholder: "10" },
    { key: "concurrency", label: "Concurrency", widget: "number", placeholder: "1" },
    { key: "timeout_seconds", label: "Timeout (s)", widget: "number", placeholder: "300" },
  ],
  static_site: [
    {
      key: "static_build_command",
      label: "Build command",
      widget: "text",
      placeholder: "npm run build",
    },
    { key: "static_output_dir", label: "Output dir", widget: "text", placeholder: "dist" },
    { key: "static_spa", label: "SPA rewrite", widget: "toggle" },
    { key: "static_index", label: "Index document", widget: "text", placeholder: "index.html" },
  ],
  faas: [
    {
      key: "faas_package_type",
      label: "Package type",
      widget: "select",
      options: FAAS_PACKAGE_TYPES,
    },
    { key: "faas_runtime", label: "Runtime", widget: "text", placeholder: "python3.12" },
    { key: "faas_handler", label: "Handler", widget: "text", placeholder: "app.handler" },
    { key: "faas_memory_mb", label: "Memory (MB)", widget: "number", placeholder: "512" },
    { key: "faas_timeout_seconds", label: "Timeout (s)", widget: "number", placeholder: "30" },
    { key: "faas_architecture", label: "Architecture", widget: "text", placeholder: "arm64" },
    { key: "faas_public", label: "Public URL", widget: "toggle" },
    { key: "faas_build_command", label: "Build command", widget: "text" },
    { key: "faas_output_dir", label: "Output dir", widget: "text" },
  ],
};

export function tuningFields(kind: string): readonly FieldSpec[] {
  return KIND_TUNING[kind] ?? [];
}

// ─── Validation (mirrors astrolift_manifest/parser.py hard rules) ────────────

export interface ManifestErr {
  path: string;
  message: string;
}

function tuningStr(w: ManifestModel["workloads"][number], key: string): string {
  const v = w.extra[key];
  return typeof v === "string" ? v : "";
}

export function validateManifest(model: ManifestModel): ManifestErr[] {
  const errs: ManifestErr[] = [];
  if (model.name.trim() === "") {
    errs.push({ path: "name", message: "Top-level app name is required." });
  }

  const seen = new Set<string>();
  model.workloads.forEach((w, i) => {
    const p = `workloads[${i}]`;
    if (w.name.trim() === "")
      errs.push({ path: `${p}.name`, message: "Workload name is required." });
    else if (seen.has(w.name))
      errs.push({ path: `${p}.name`, message: `Duplicate workload name "${w.name}".` });
    seen.add(w.name);

    if (!WORKLOAD_KINDS.includes(w.kind as (typeof WORKLOAD_KINDS)[number])) {
      errs.push({
        path: `${p}.kind`,
        message: `Kind must be one of ${WORKLOAD_KINDS.join(", ")}.`,
      });
    }
    if (w.kind === "cronjob" && w.schedule.trim() === "") {
      errs.push({ path: `${p}.schedule`, message: "Cronjob workloads require a schedule." });
    }
    if (
      w.concurrency_policy &&
      !CONCURRENCY_POLICIES.includes(w.concurrency_policy as (typeof CONCURRENCY_POLICIES)[number])
    ) {
      errs.push({
        path: `${p}.concurrency_policy`,
        message: `Concurrency policy must be one of ${CONCURRENCY_POLICIES.join(", ")}.`,
      });
    }
    if (w.kind === "workflow") {
      if (tuningStr(w, "workflow_type").trim() === "")
        errs.push({
          path: `${p}.workflow_type`,
          message: "Workflow workloads require a workflow type.",
        });
      if (tuningStr(w, "task_queue").trim() === "")
        errs.push({ path: `${p}.task_queue`, message: "Workflow workloads require a task queue." });
    }
    if (
      w.kind === "static_site" &&
      tuningStr(w, "static_build_command").trim() !== "" &&
      tuningStr(w, "static_output_dir").trim() === ""
    ) {
      errs.push({
        path: `${p}.static_output_dir`,
        message: "A build command requires an output dir.",
      });
    }
    if (w.kind === "faas") {
      const pkg = tuningStr(w, "faas_package_type") || "image";
      if (pkg === "zip") {
        for (const k of ["faas_runtime", "faas_handler", "faas_output_dir"] as const) {
          if (tuningStr(w, k).trim() === "")
            errs.push({ path: `${p}.${k}`, message: `zip-package FaaS requires ${k}.` });
        }
      } else if (
        tuningStr(w, "faas_handler").trim() !== "" ||
        tuningStr(w, "faas_runtime").trim() !== ""
      ) {
        errs.push({
          path: `${p}.faas_handler`,
          message: "image-package FaaS must not set handler/runtime.",
        });
      }
    }
    if (!supportsContainers(w.kind) && w.containers.length > 0) {
      errs.push({
        path: `${p}.containers`,
        message: `${w.kind} workloads must declare no containers.`,
      });
    }

    w.containers.forEach((c, ci) => {
      if (c.name.trim() === "")
        errs.push({ path: `${p}.containers[${ci}].name`, message: "Container name is required." });
      if (!HEALTHCHECK_KINDS.includes(c.healthcheck.kind as (typeof HEALTHCHECK_KINDS)[number]))
        errs.push({
          path: `${p}.containers[${ci}].healthcheck`,
          message: `Healthcheck kind must be one of ${HEALTHCHECK_KINDS.join(", ")}.`,
        });
    });
  });

  model.managedServices.forEach((s, i) => {
    if (s.kind.trim() === "")
      errs.push({
        path: `managedServices[${i}].kind`,
        message: "Managed service kind is required.",
      });
  });

  return errs;
}

export function errorFor(errors: ManifestErr[], path: string): string | undefined {
  return errors.find((e) => e.path === path)?.message;
}

export function hasErrorPrefix(errors: ManifestErr[], prefix: string): boolean {
  return errors.some((e) => e.path === prefix || e.path.startsWith(prefix + "."));
}
