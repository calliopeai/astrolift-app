/**
 * Client-side mirror of the backend ``astrolift_manifest.schema_detect``
 * classifier (#1172). Two distinct schemas share the ``astrolift.toml``
 * filename:
 *
 *   - an **app manifest** — a deployable app, keyed by a top-level ``name``
 *     and/or one or more ``[[workloads]]`` blocks; and
 *   - an **agent config repo** — a reusable skills/tools library, keyed by
 *     ``astrolift_version`` plus ``[skills.*]`` / ``[tools.*]`` tables and
 *     carrying neither a top-level ``name`` nor ``[[workloads]]``.
 *
 * This is a cheap, tolerant, regex-grade classifier — the same posture as the
 * wizard's manifest checks and the backend's ``detect_toml_schema``. It is
 * deliberately NOT a TOML parse: the scoped ``toml`` codec can't handle the
 * multi-line strings that agent skill ``content`` fields routinely use, so a
 * parse-based detector would misclassify a valid agent config as ``unknown``.
 * Regexes keep detection robust regardless of what the editing codec supports;
 * the backend remains authoritative.
 */

export type TomlSchemaFamily = "app" | "agent_config" | "unknown";

/**
 * Classify an ``astrolift.toml`` body. Mirrors backend ``detect_toml_schema``:
 *
 *   - ``"app"`` — has a top-level ``name`` or at least one ``[[workloads]]``
 *     block. Checked first, so a file carrying *both* a ``name`` and
 *     ``[skills.*]`` tables classifies as an app (its deployable surface wins).
 *   - ``"agent_config"`` — declares ``astrolift_version`` plus a top-level
 *     ``[skills.*]`` / ``[tools.*]`` table, and has neither a top-level
 *     ``name`` nor ``[[workloads]]``.
 *   - ``"unknown"`` — anything else, including an empty body.
 */
export function detectTomlSchema(raw: string): TomlSchemaFamily {
  const trimmed = raw.trim();
  if (!trimmed) return "unknown";

  // Top-level `name = "..."` lives before the first `[section]` header.
  const beforeFirstSection = trimmed.split(/\n\[/)[0];
  const hasName = /^name\s*=\s*["'][^"'\n]+["']/m.test(beforeFirstSection);
  const hasWorkloads = /^\s*\[\[\s*workloads\s*\]\]/m.test(trimmed);
  // App wins the tie (backend checks the deployable surface first).
  if (hasName || hasWorkloads) return "app";

  const hasVersion = /^astrolift_version\s*=/m.test(trimmed);
  const hasSkillOrToolTable = /^\s*\[\s*(?:skills|tools)\s*\./m.test(trimmed);
  if (hasVersion && hasSkillOrToolTable) return "agent_config";

  return "unknown";
}
