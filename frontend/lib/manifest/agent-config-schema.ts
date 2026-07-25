/**
 * Validation + field hints for the agent-config visual builder (#1172). Sibling
 * to `schema.ts` (the app manifest descriptor): it reuses that module's generic
 * `ManifestErr` shape and lookup helpers and mirrors the backend consumers'
 * hard rules (`skill_importer` upserts keyed on a slug; ToolDef.Adapter is a
 * bounded enum) so the form flags what an import/assembly would reject.
 */

import { parseJsonObjectField, type AgentConfigModel } from "./agent-config-model";
import type { ManifestErr } from "./schema";

export { errorFor, hasErrorPrefix } from "./schema";
export type { ManifestErr } from "./schema";

/** ToolDef.Adapter enum values — a select hint; backend defaults invalid → python_fn. */
export const TOOL_ADAPTERS = ["python_fn", "http_endpoint", "mcp_server"] as const;

const JSON_FIELDS: ReadonlyArray<{
  key: "input_schema" | "output_schema" | "implementation_config";
  label: string;
}> = [
  { key: "input_schema", label: "Input schema" },
  { key: "output_schema", label: "Output schema" },
  { key: "implementation_config", label: "Implementation config" },
];

export function validateAgentConfig(model: AgentConfigModel): ManifestErr[] {
  const errs: ManifestErr[] = [];

  if (model.astrolift_version.trim() === "") {
    errs.push({ path: "astrolift_version", message: "astrolift_version is required for an agent config." });
  }

  const skillSlugs = new Set<string>();
  model.skills.forEach((s, i) => {
    const p = `skills[${i}]`;
    if (s.slug.trim() === "") errs.push({ path: `${p}.slug`, message: "Skill slug is required." });
    else if (skillSlugs.has(s.slug)) errs.push({ path: `${p}.slug`, message: `Duplicate skill slug "${s.slug}".` });
    skillSlugs.add(s.slug);
  });

  const toolSlugs = new Set<string>();
  model.tools.forEach((t, i) => {
    const p = `tools[${i}]`;
    if (t.slug.trim() === "") errs.push({ path: `${p}.slug`, message: "Tool slug is required." });
    else if (toolSlugs.has(t.slug)) errs.push({ path: `${p}.slug`, message: `Duplicate tool slug "${t.slug}".` });
    toolSlugs.add(t.slug);
    for (const { key, label } of JSON_FIELDS) {
      const { error } = parseJsonObjectField(t[key]);
      if (error) errs.push({ path: `${p}.${key}`, message: `${label}: ${error}` });
    }
  });

  model.environment.vars.forEach((e, i) => {
    if (e.key === "tool_preset" || e.key === "allow_install") {
      errs.push({
        path: `environment.vars[${i}]`,
        message: `"${e.key}" is a reserved key — set it in the field above.`,
      });
    }
  });

  return errs;
}
