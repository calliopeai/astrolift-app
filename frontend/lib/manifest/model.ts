/**
 * Typed manifest model + lossless mapping to/from the TOML value tree (#1110).
 *
 * The model surfaces the fields the visual builder edits (drawn from the
 * backend contract in astrolift_manifest/parser.py + types.py). Every key the
 * model doesn't explicitly own is carried verbatim in an `extra` bag at the
 * matching level (top-level / per-workload / per-container / per-service), so
 * `modelToToml(tomlToModel(x))` is semantically lossless — the form never
 * drops a workload volume, a `[[jobs]]` block, or an `astrolift_version` pin.
 *
 * Per-kind workload tuning (agent / workflow / function / static_site / faas)
 * lives in the workload `extra` bag and is edited through the schema
 * descriptor, so the model stays compact while remaining exhaustive.
 */

import { parseToml, roundTripSafe, serializeToml, type TomlTable, type TomlValue } from "./toml";

export const WORKLOAD_KINDS = [
  "deployment",
  "statefulset",
  "job",
  "cronjob",
  "task",
  "agent",
  "workflow",
  "function",
  "static_site",
  "faas",
] as const;

export type WorkloadKind = (typeof WORKLOAD_KINDS)[number];

export interface EnvEntry {
  key: string;
  /** Usually a string; inline-table / numeric values are preserved verbatim. */
  value: TomlValue;
}

export interface HealthcheckModel {
  kind: string; // none | http | tcp | exec
  value: string;
  port: number | null;
}

export interface ContainerModel {
  name: string;
  is_primary: boolean;
  image_ref: string;
  dockerfile_path: string;
  build_context: string;
  port: number | null;
  command: string[];
  args: string[];
  env: EnvEntry[];
  healthcheck: HealthcheckModel;
  extra: TomlTable;
}

export interface WorkloadModel {
  name: string;
  kind: string;
  is_public: boolean;
  replicas: number | null;
  cpu_request: string;
  cpu_limit: string;
  memory_request: string;
  memory_limit: string;
  hpa_min: number | null;
  hpa_max: number | null;
  hpa_target_cpu_pct: number | null;
  storage_class: string;
  storage_size: string;
  schedule: string;
  concurrency_policy: string;
  containers: ContainerModel[];
  /** Per-kind tuning + any unmodeled workload keys (volumes, …). */
  extra: TomlTable;
}

export interface ManagedServiceModel {
  kind: string;
  name: string;
  variant: string;
  config: EnvEntry[];
  extra: TomlTable;
}

export interface ManifestModel {
  name: string;
  env: EnvEntry[];
  workloads: WorkloadModel[];
  managedServices: ManagedServiceModel[];
  /** Top-level keys the form doesn't model (astrolift_version, app, brief, …). */
  extra: TomlTable;
}

// ─── helpers ─────────────────────────────────────────────────────────────────

function isPlainObject(v: unknown): v is TomlTable {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function asString(v: TomlValue | undefined): string {
  return typeof v === "string" ? v : "";
}

function asNumberOrNull(v: TomlValue | undefined): number | null {
  return typeof v === "number" ? v : null;
}

function asBool(v: TomlValue | undefined): boolean {
  return v === true;
}

function asStringArray(v: TomlValue | undefined): string[] {
  return Array.isArray(v) ? v.map((x) => (typeof x === "string" ? x : String(x))) : [];
}

function tableToEntries(v: TomlValue | undefined): EnvEntry[] {
  if (!isPlainObject(v)) return [];
  return Object.entries(v).map(([key, value]) => ({ key, value }));
}

function entriesToTable(entries: EnvEntry[]): TomlTable {
  const out: TomlTable = {};
  for (const e of entries) {
    if (e.key.trim() === "") continue;
    out[e.key] = e.value;
  }
  return out;
}

type Claim = (v: TomlValue) => boolean;
const isStr: Claim = (v) => typeof v === "string";
const isNum: Claim = (v) => typeof v === "number";
const isBool: Claim = (v) => typeof v === "boolean";
const isArr: Claim = (v) => Array.isArray(v);
const isObj: Claim = (v) => isPlainObject(v);

/**
 * The passthrough bag for one level: every source key whose value the model
 * does NOT faithfully claim. A key is claimed only when its value has the
 * expected type — so a wrongly-typed value (e.g. a legacy string `healthcheck`
 * where the model expects a table) is preserved verbatim rather than dropped.
 * This keeps `modelToToml(tomlToModel(x))` lossless even for off-contract input.
 */
function unclaimed(src: TomlTable, claims: Record<string, Claim>): TomlTable {
  const out: TomlTable = {};
  for (const [k, v] of Object.entries(src)) {
    const claim = claims[k];
    if (claim && claim(v)) continue;
    out[k] = v;
  }
  return out;
}

/** Assign only when the value is meaningfully set, so absent keys stay absent. */
function setStr(obj: TomlTable, key: string, val: string): void {
  if (val.trim() !== "") obj[key] = val;
}
function setNum(obj: TomlTable, key: string, val: number | null): void {
  if (val !== null && !Number.isNaN(val)) obj[key] = val;
}
function setBool(obj: TomlTable, key: string, val: boolean): void {
  if (val) obj[key] = true;
}
function setList(obj: TomlTable, key: string, val: string[]): void {
  if (val.length > 0) obj[key] = val;
}

const CONTAINER_CLAIMS: Record<string, Claim> = {
  name: isStr,
  is_primary: isBool,
  image_ref: isStr,
  dockerfile_path: isStr,
  build_context: isStr,
  port: isNum,
  command: isArr,
  args: isArr,
  env: isObj,
  healthcheck: isObj,
};

const WORKLOAD_CLAIMS: Record<string, Claim> = {
  name: isStr,
  kind: isStr,
  is_public: isBool,
  replicas: isNum,
  cpu_request: isStr,
  cpu_limit: isStr,
  memory_request: isStr,
  memory_limit: isStr,
  hpa_min: isNum,
  hpa_max: isNum,
  hpa_target_cpu_pct: isNum,
  storage_class: isStr,
  storage_size: isStr,
  schedule: isStr,
  concurrency_policy: isStr,
  containers: isArr,
};

const SERVICE_CLAIMS: Record<string, Claim> = {
  kind: isStr,
  name: isStr,
  variant: isStr,
  config: isObj,
};

const TOP_CLAIMS: Record<string, Claim> = {
  name: isStr,
  env: isObj,
  workloads: isArr,
  managed_services: isArr,
};

// ─── TOML → model ────────────────────────────────────────────────────────────

function parseContainer(c: TomlTable): ContainerModel {
  const hc = isPlainObject(c.healthcheck) ? c.healthcheck : {};
  return {
    name: asString(c.name),
    is_primary: asBool(c.is_primary),
    image_ref: asString(c.image_ref),
    dockerfile_path: asString(c.dockerfile_path),
    build_context: asString(c.build_context),
    port: asNumberOrNull(c.port),
    command: asStringArray(c.command),
    args: asStringArray(c.args),
    env: tableToEntries(c.env),
    healthcheck: {
      kind: asString(hc.kind) || "none",
      value: asString(hc.value),
      port: asNumberOrNull(hc.port),
    },
    extra: unclaimed(c, CONTAINER_CLAIMS),
  };
}

function parseWorkload(w: TomlTable): WorkloadModel {
  const containers = Array.isArray(w.containers)
    ? w.containers.filter(isPlainObject).map(parseContainer)
    : [];
  return {
    name: asString(w.name),
    kind: asString(w.kind) || "deployment",
    is_public: asBool(w.is_public),
    replicas: asNumberOrNull(w.replicas),
    cpu_request: asString(w.cpu_request),
    cpu_limit: asString(w.cpu_limit),
    memory_request: asString(w.memory_request),
    memory_limit: asString(w.memory_limit),
    hpa_min: asNumberOrNull(w.hpa_min),
    hpa_max: asNumberOrNull(w.hpa_max),
    hpa_target_cpu_pct: asNumberOrNull(w.hpa_target_cpu_pct),
    storage_class: asString(w.storage_class),
    storage_size: asString(w.storage_size),
    schedule: asString(w.schedule),
    concurrency_policy: asString(w.concurrency_policy),
    containers,
    extra: unclaimed(w, WORKLOAD_CLAIMS),
  };
}

function parseService(s: TomlTable): ManagedServiceModel {
  return {
    kind: asString(s.kind),
    name: asString(s.name),
    variant: asString(s.variant),
    config: tableToEntries(s.config),
    extra: unclaimed(s, SERVICE_CLAIMS),
  };
}

export interface ParseModelResult {
  model: ManifestModel;
  safe: boolean;
  reason?: string;
}

/**
 * Parse a manifest into the editable model. `safe` is false when the codec
 * can't guarantee a round-trip — callers keep the raw editor in that case.
 * An empty/whitespace manifest yields an empty (but safe) model to seed a
 * from-scratch build.
 */
export function tomlToModel(text: string): ParseModelResult {
  if (text.trim() === "") {
    return { model: emptyModel(), safe: true };
  }
  const rt = roundTripSafe(text);
  if (!rt.safe) {
    return { model: emptyModel(), safe: false, reason: rt.reason };
  }
  const obj = parseToml(text);
  const model: ManifestModel = {
    name: asString(obj.name),
    env: tableToEntries(obj.env),
    workloads: Array.isArray(obj.workloads)
      ? obj.workloads.filter(isPlainObject).map(parseWorkload)
      : [],
    managedServices: Array.isArray(obj.managed_services)
      ? obj.managed_services.filter(isPlainObject).map(parseService)
      : [],
    extra: unclaimed(obj, TOP_CLAIMS),
  };
  return { model, safe: true };
}

// ─── model → TOML ────────────────────────────────────────────────────────────

function containerToObject(c: ContainerModel): TomlTable {
  const obj: TomlTable = {};
  setStr(obj, "name", c.name);
  setBool(obj, "is_primary", c.is_primary);
  setStr(obj, "image_ref", c.image_ref);
  setStr(obj, "dockerfile_path", c.dockerfile_path);
  setStr(obj, "build_context", c.build_context);
  setNum(obj, "port", c.port);
  setList(obj, "command", c.command);
  setList(obj, "args", c.args);
  const env = entriesToTable(c.env);
  if (Object.keys(env).length > 0) obj.env = env;
  Object.assign(obj, c.extra);
  if (c.healthcheck.kind && c.healthcheck.kind !== "none") {
    const hc: TomlTable = { kind: c.healthcheck.kind };
    setStr(hc, "value", c.healthcheck.value);
    setNum(hc, "port", c.healthcheck.port);
    obj.healthcheck = hc;
  }
  return obj;
}

function workloadToObject(w: WorkloadModel): TomlTable {
  const obj: TomlTable = {};
  setStr(obj, "name", w.name);
  setStr(obj, "kind", w.kind);
  setBool(obj, "is_public", w.is_public);
  setNum(obj, "replicas", w.replicas);
  setStr(obj, "cpu_request", w.cpu_request);
  setStr(obj, "cpu_limit", w.cpu_limit);
  setStr(obj, "memory_request", w.memory_request);
  setStr(obj, "memory_limit", w.memory_limit);
  setNum(obj, "hpa_min", w.hpa_min);
  setNum(obj, "hpa_max", w.hpa_max);
  setNum(obj, "hpa_target_cpu_pct", w.hpa_target_cpu_pct);
  setStr(obj, "storage_class", w.storage_class);
  setStr(obj, "storage_size", w.storage_size);
  setStr(obj, "schedule", w.schedule);
  setStr(obj, "concurrency_policy", w.concurrency_policy);
  // Per-kind tuning + unmodeled keys ride in extra (already correctly typed).
  Object.assign(obj, w.extra);
  if (w.containers.length > 0) {
    obj.containers = w.containers.map(containerToObject);
  }
  return obj;
}

function serviceToObject(s: ManagedServiceModel): TomlTable {
  const obj: TomlTable = {};
  setStr(obj, "kind", s.kind);
  setStr(obj, "name", s.name);
  setStr(obj, "variant", s.variant);
  Object.assign(obj, s.extra);
  const config = entriesToTable(s.config);
  if (Object.keys(config).length > 0) obj.config = config;
  return obj;
}

export function modelToObject(model: ManifestModel): TomlTable {
  const obj: TomlTable = {};
  setStr(obj, "name", model.name);
  const env = entriesToTable(model.env);
  if (Object.keys(env).length > 0) obj.env = env;
  if (model.workloads.length > 0) obj.workloads = model.workloads.map(workloadToObject);
  if (model.managedServices.length > 0) {
    obj.managed_services = model.managedServices.map(serviceToObject);
  }
  Object.assign(obj, model.extra);
  return obj;
}

export function modelToToml(model: ManifestModel): string {
  return serializeToml(modelToObject(model));
}

// ─── factories ───────────────────────────────────────────────────────────────

export function emptyModel(): ManifestModel {
  return { name: "", env: [], workloads: [], managedServices: [], extra: {} };
}

export function emptyContainer(name = "app"): ContainerModel {
  return {
    name,
    is_primary: true,
    image_ref: "",
    dockerfile_path: "",
    build_context: "",
    port: null,
    command: [],
    args: [],
    env: [],
    healthcheck: { kind: "none", value: "", port: null },
    extra: {},
  };
}

export function emptyWorkload(name = "web"): WorkloadModel {
  return {
    name,
    kind: "deployment",
    is_public: false,
    replicas: null,
    cpu_request: "",
    cpu_limit: "",
    memory_request: "",
    memory_limit: "",
    hpa_min: null,
    hpa_max: null,
    hpa_target_cpu_pct: null,
    storage_class: "",
    storage_size: "",
    schedule: "",
    concurrency_policy: "",
    containers: [emptyContainer()],
    extra: {},
  };
}

export function emptyService(kind = "postgres"): ManagedServiceModel {
  return { kind, name: "", variant: "", config: [], extra: {} };
}
