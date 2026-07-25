/**
 * Typed agent-config model + lossless mapping to/from the TOML value tree
 * (#1172). Sibling to `model.ts` (the app manifest model): same scoped,
 * dependency-free codec (`./toml`), same `extra`-bag passthrough discipline so
 * `agentModelToToml(tomlToAgentModel(x))` is semantically lossless.
 *
 * The agent config repo is a reusable skills/tools *library* — keyed by
 * `astrolift_version` with `[skills.<slug>]` / `[tools.<slug>]` tables (keyed
 * by slug, NOT arrays-of-tables) and an `[environment]` table. Field surface is
 * drawn from the backend consumers `astrolift_agents.services.skill_importer`
 * (Skill / ToolDef upsert) and `brief_assembler` (`[environment]` reserved
 * keys). Every key the model doesn't own is carried verbatim in an `extra` bag
 * at the matching level (top-level / per-skill / per-tool), so `[agent]`,
 * `[roster.*]`, `[secrets]`, a tool's `skill` binding, etc. round-trip
 * untouched.
 */

import type { EnvEntry } from "./model";
import { parseToml, roundTripSafe, serializeToml, type TomlTable, type TomlValue } from "./toml";

// Reserved [environment] keys consumed structurally, not as literal env vars
// (mirrors backend brief_assembler `_ENVIRONMENT_RESERVED_KEYS`).
export const ENVIRONMENT_RESERVED_KEYS = ["tool_preset", "allow_install"] as const;

export interface AgentSkillModel {
  slug: string;
  name: string;
  description: string;
  /** Instruction body. `system_prompt` is an accepted alias — surfaced here. */
  content: string;
  /** Which TOML key `content` round-trips through, so an alias stays an alias. */
  contentKey: "content" | "system_prompt";
  dependencies: string[];
  agent_type: string;
  scaffolding_tags: string[];
  is_active: boolean;
  /** Per-skill keys the form doesn't model (e.g. `tools`, `skill`). */
  extra: TomlTable;
}

export interface AgentToolModel {
  slug: string;
  name: string;
  description: string;
  adapter: string;
  handler_ref: string;
  /** JSON-object fields, edited as raw JSON text; empty string = absent. */
  input_schema: string;
  output_schema: string;
  implementation_config: string;
  commands: string[];
  required_packages: string[];
  capability_group: string;
  agent_type_bindings: string[];
  is_builtin: boolean;
  /** Per-tool keys the form doesn't model (e.g. the `skill` binding). */
  extra: TomlTable;
}

export interface AgentEnvModel {
  tool_preset: string;
  allow_install: boolean;
  /** Literal env vars — everything under `[environment]` bar the reserved keys. */
  vars: EnvEntry[];
}

export interface AgentConfigModel {
  astrolift_version: string;
  environment: AgentEnvModel;
  skills: AgentSkillModel[];
  tools: AgentToolModel[];
  /** Top-level keys the form doesn't model (`[agent]`, `[roster.*]`, …). */
  extra: TomlTable;
}

// ─── helpers ─────────────────────────────────────────────────────────────────

function isPlainObject(v: unknown): v is TomlTable {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function asString(v: TomlValue | undefined): string {
  return typeof v === "string" ? v : "";
}

function asBool(v: TomlValue | undefined): boolean {
  return v === true;
}

function asStringArray(v: TomlValue | undefined): string[] {
  return Array.isArray(v) ? v.map((x) => (typeof x === "string" ? x : String(x))) : [];
}

type Claim = (v: TomlValue) => boolean;
const isStr: Claim = (v) => typeof v === "string";
const isBool: Claim = (v) => typeof v === "boolean";
const isArr: Claim = (v) => Array.isArray(v);
const isObj: Claim = (v) => isPlainObject(v);

/**
 * The passthrough bag for one level: every source key whose value the model
 * does NOT faithfully claim (mirrors `model.ts`). A key is claimed only when
 * its value has the expected type, so a wrongly-typed value is preserved
 * verbatim rather than dropped.
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

/** Assign only when meaningfully set, so absent keys stay absent. */
function setStr(obj: TomlTable, key: string, val: string): void {
  if (val.trim() !== "") obj[key] = val;
}
function setBool(obj: TomlTable, key: string, val: boolean): void {
  if (val) obj[key] = true;
}
function setList(obj: TomlTable, key: string, val: string[]): void {
  if (val.length > 0) obj[key] = val;
}

/** Pretty-print a JSON-object field (input_schema, …) for a textarea. */
function tableToJsonText(v: TomlValue | undefined): string {
  return isPlainObject(v) ? JSON.stringify(v, null, 2) : "";
}

/**
 * Coerce parsed JSON into a TOML value tree; throws on anything TOML can't hold
 * (null/undefined) so a bad field fails safe instead of emitting broken TOML.
 */
function jsonValueToToml(v: unknown): TomlValue {
  if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") return v;
  if (Array.isArray(v)) return v.map(jsonValueToToml);
  if (v !== null && typeof v === "object") {
    const out: TomlTable = {};
    for (const [k, val] of Object.entries(v)) out[k] = jsonValueToToml(val);
    return out;
  }
  throw new Error("value is not representable in TOML (e.g. null)");
}

/**
 * Parse a JSON-object textarea into a TomlTable. Empty text → `{ table: null }`
 * (the field is absent). Invalid JSON, a non-object, or a value TOML can't
 * hold → `{ table: null, error }` so callers can both omit the key (fail-safe
 * serialize) and surface the error (validation) without ever emitting broken
 * TOML or silently corrupting the field.
 */
export function parseJsonObjectField(text: string): { table: TomlTable | null; error?: string } {
  const trimmed = text.trim();
  if (trimmed === "") return { table: null };
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch (e) {
    return { table: null, error: e instanceof Error ? e.message : "invalid JSON" };
  }
  if (!isPlainObject(parsed)) return { table: null, error: "must be a JSON object" };
  try {
    return { table: jsonValueToToml(parsed) as TomlTable };
  } catch (e) {
    return { table: null, error: e instanceof Error ? e.message : "not representable in TOML" };
  }
}

const SKILL_CLAIMS: Record<string, Claim> = {
  name: isStr,
  description: isStr,
  dependencies: isArr,
  agent_type: isStr,
  scaffolding_tags: isArr,
  is_active: isBool,
  // content / system_prompt are handled specially (alias-aware) below.
};

const TOOL_CLAIMS: Record<string, Claim> = {
  name: isStr,
  description: isStr,
  adapter: isStr,
  handler_ref: isStr,
  input_schema: isObj,
  output_schema: isObj,
  implementation_config: isObj,
  commands: isArr,
  required_packages: isArr,
  capability_group: isStr,
  agent_type_bindings: isArr,
  is_builtin: isBool,
};

const TOP_CLAIMS: Record<string, Claim> = {
  astrolift_version: isStr,
  environment: isObj,
  skills: isObj,
  tools: isObj,
};

// ─── TOML → model ────────────────────────────────────────────────────────────

function parseSkill(slug: string, src: TomlTable): AgentSkillModel {
  const extra = unclaimed(src, SKILL_CLAIMS);
  // `content` is canonical; `system_prompt` an accepted alias. Surface whichever
  // the source used and remember it so the round-trip keeps the alias an alias.
  // The non-chosen key (if present) stays in `extra` and round-trips untouched.
  let content = "";
  let contentKey: "content" | "system_prompt" = "content";
  if (typeof src.content === "string") {
    content = src.content;
    contentKey = "content";
    delete extra.content;
  } else if (typeof src.system_prompt === "string") {
    content = src.system_prompt;
    contentKey = "system_prompt";
    delete extra.system_prompt;
  }
  return {
    slug,
    name: asString(src.name),
    description: asString(src.description),
    content,
    contentKey,
    dependencies: asStringArray(src.dependencies),
    agent_type: asString(src.agent_type),
    scaffolding_tags: asStringArray(src.scaffolding_tags),
    // Backend default is True — absent means active.
    is_active: "is_active" in src ? asBool(src.is_active) : true,
    extra,
  };
}

function parseTool(slug: string, src: TomlTable): AgentToolModel {
  return {
    slug,
    name: asString(src.name),
    description: asString(src.description),
    adapter: asString(src.adapter),
    handler_ref: asString(src.handler_ref),
    input_schema: tableToJsonText(src.input_schema),
    output_schema: tableToJsonText(src.output_schema),
    implementation_config: tableToJsonText(src.implementation_config),
    commands: asStringArray(src.commands),
    required_packages: asStringArray(src.required_packages),
    capability_group: asString(src.capability_group),
    agent_type_bindings: asStringArray(src.agent_type_bindings),
    is_builtin: asBool(src.is_builtin),
    extra: unclaimed(src, TOOL_CLAIMS),
  };
}

function parseEnv(src: TomlValue | undefined): AgentEnvModel {
  const env = isPlainObject(src) ? src : {};
  const vars: EnvEntry[] = [];
  for (const [k, v] of Object.entries(env)) {
    // Claim a reserved key only when its value has the expected type; an
    // off-contract value falls through to the literal-var editor (preserved).
    if (k === "tool_preset" && typeof v === "string") continue;
    if (k === "allow_install" && typeof v === "boolean") continue;
    vars.push({ key: k, value: v });
  }
  return {
    tool_preset: asString(env.tool_preset),
    allow_install: asBool(env.allow_install),
    vars,
  };
}

export interface ParseAgentModelResult {
  model: AgentConfigModel;
  safe: boolean;
  reason?: string;
}

/**
 * Parse an agent config into the editable model. `safe` is false when the codec
 * can't guarantee a round-trip (e.g. multi-line strings, which agent skill
 * `content` fields often use) — callers keep the raw editor in that case, just
 * like the app pane. An empty body yields an empty (but safe) model.
 */
export function tomlToAgentModel(text: string): ParseAgentModelResult {
  if (text.trim() === "") {
    return { model: emptyAgentConfig(), safe: true };
  }
  const rt = roundTripSafe(text);
  if (!rt.safe) {
    return { model: emptyAgentConfig(), safe: false, reason: rt.reason };
  }
  const obj = parseToml(text);
  const skillsTable = isPlainObject(obj.skills) ? obj.skills : {};
  const toolsTable = isPlainObject(obj.tools) ? obj.tools : {};
  const model: AgentConfigModel = {
    astrolift_version: asString(obj.astrolift_version),
    environment: parseEnv(obj.environment),
    skills: Object.entries(skillsTable)
      .filter((entry): entry is [string, TomlTable] => isPlainObject(entry[1]))
      .map(([slug, v]) => parseSkill(slug, v)),
    tools: Object.entries(toolsTable)
      .filter((entry): entry is [string, TomlTable] => isPlainObject(entry[1]))
      .map(([slug, v]) => parseTool(slug, v)),
    extra: unclaimed(obj, TOP_CLAIMS),
  };
  return { model, safe: true };
}

// ─── model → TOML ────────────────────────────────────────────────────────────

function skillToObject(s: AgentSkillModel): TomlTable {
  const obj: TomlTable = {};
  setStr(obj, "name", s.name);
  setStr(obj, "description", s.description);
  setStr(obj, s.contentKey, s.content);
  setList(obj, "dependencies", s.dependencies);
  setStr(obj, "agent_type", s.agent_type);
  setList(obj, "scaffolding_tags", s.scaffolding_tags);
  // Backend default is True — emit only the meaningful deviation (false).
  if (!s.is_active) obj.is_active = false;
  Object.assign(obj, s.extra);
  return obj;
}

function toolToObject(t: AgentToolModel): TomlTable {
  const obj: TomlTable = {};
  setStr(obj, "name", t.name);
  setStr(obj, "description", t.description);
  setStr(obj, "adapter", t.adapter);
  setStr(obj, "handler_ref", t.handler_ref);
  setList(obj, "commands", t.commands);
  setList(obj, "required_packages", t.required_packages);
  setStr(obj, "capability_group", t.capability_group);
  setList(obj, "agent_type_bindings", t.agent_type_bindings);
  setBool(obj, "is_builtin", t.is_builtin);
  // JSON-object fields: embed when valid, omit when empty/invalid (the invalid
  // case is surfaced by validation, never silently written as broken TOML).
  const input = parseJsonObjectField(t.input_schema).table;
  if (input) obj.input_schema = input;
  const output = parseJsonObjectField(t.output_schema).table;
  if (output) obj.output_schema = output;
  const impl = parseJsonObjectField(t.implementation_config).table;
  if (impl) obj.implementation_config = impl;
  Object.assign(obj, t.extra);
  return obj;
}

function envToObject(env: AgentEnvModel): TomlTable | null {
  const obj: TomlTable = {};
  setStr(obj, "tool_preset", env.tool_preset);
  setBool(obj, "allow_install", env.allow_install);
  for (const e of env.vars) {
    if (e.key.trim() === "") continue;
    // Reserved keys are owned by the dedicated fields above; never clobber them.
    if (e.key === "tool_preset" || e.key === "allow_install") continue;
    obj[e.key] = e.value;
  }
  return Object.keys(obj).length > 0 ? obj : null;
}

export function agentModelToObject(model: AgentConfigModel): TomlTable {
  const obj: TomlTable = {};
  setStr(obj, "astrolift_version", model.astrolift_version);
  const env = envToObject(model.environment);
  if (env) obj.environment = env;
  if (model.skills.length > 0) {
    const skills: TomlTable = {};
    for (const s of model.skills) {
      if (s.slug.trim() === "") continue;
      skills[s.slug] = skillToObject(s);
    }
    if (Object.keys(skills).length > 0) obj.skills = skills;
  }
  if (model.tools.length > 0) {
    const tools: TomlTable = {};
    for (const t of model.tools) {
      if (t.slug.trim() === "") continue;
      tools[t.slug] = toolToObject(t);
    }
    if (Object.keys(tools).length > 0) obj.tools = tools;
  }
  Object.assign(obj, model.extra);
  return obj;
}

export function agentModelToToml(model: AgentConfigModel): string {
  return serializeToml(agentModelToObject(model));
}

// ─── factories ───────────────────────────────────────────────────────────────

export function emptyAgentConfig(): AgentConfigModel {
  return {
    astrolift_version: "",
    environment: { tool_preset: "", allow_install: false, vars: [] },
    skills: [],
    tools: [],
    extra: {},
  };
}

export function emptySkill(slug = "skill"): AgentSkillModel {
  return {
    slug,
    name: "",
    description: "",
    content: "",
    contentKey: "content",
    dependencies: [],
    agent_type: "",
    scaffolding_tags: [],
    is_active: true,
    extra: {},
  };
}

export function emptyTool(slug = "tool"): AgentToolModel {
  return {
    slug,
    name: "",
    description: "",
    adapter: "",
    handler_ref: "",
    input_schema: "",
    output_schema: "",
    implementation_config: "",
    commands: [],
    required_packages: [],
    capability_group: "",
    agent_type_bindings: [],
    is_builtin: false,
    extra: {},
  };
}
