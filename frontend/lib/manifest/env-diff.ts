/**
 * Client-side, best-effort mirror of the backend's
 * ``astrolift_manifest.env_edit.read_app_env`` -- extracts the top-level
 * ``[env]`` table from raw manifest TOML text. Line-based, not a real TOML
 * parse (same posture as ``schema-detect.ts``, which explains why: the
 * scoped codec can't handle every value shape a hand-edited manifest can
 * carry). Good enough to tell an operator which keys a staged edit is about
 * to change before they confirm `applyStagedManifest` -- the backend's own
 * parse is what actually gates the mutation (#1759 adversarial review, M4).
 */

function parseEnvTable(raw: string): Record<string, string> {
  const lines = (raw ?? "").split("\n");
  const out: Record<string, string> = {};
  let inEnvTable = false;
  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    if (line.startsWith("[")) {
      inEnvTable = line === "[env]";
      continue;
    }
    if (!inEnvTable) continue;
    const eq = line.indexOf("=");
    if (eq === -1) continue;
    const key = line.slice(0, eq).trim();
    if (key) out[key] = line.slice(eq + 1).trim();
  }
  return out;
}

/**
 * Key names added, changed, or removed between two manifest bodies'
 * top-level ``[env]`` tables, sorted. Values are compared only to decide
 * whether a key changed -- never returned, so a caller rendering this list
 * in a confirm dialog can't leak a secret literal by construction.
 */
export function changedEnvKeyNames(before: string, after: string): string[] {
  const beforeEnv = parseEnvTable(before);
  const afterEnv = parseEnvTable(after);
  const keys = new Set([...Object.keys(beforeEnv), ...Object.keys(afterEnv)]);
  const changed: string[] = [];
  for (const key of keys) {
    if (beforeEnv[key] !== afterEnv[key]) changed.push(key);
  }
  return changed.sort();
}
