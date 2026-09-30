/**
 * Slug helpers shared by the project and team create / edit sheets.
 */

/** The create sheets' auto-slug: derived from the display name until the slug is edited. */
export function slugify(value: string): string {
  const normalized = value
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  if (!normalized) return "";
  const alphaStart = /^[a-z]/.test(normalized) ? normalized : `x-${normalized}`;
  return alphaStart.slice(0, SLUG_MAX).replace(/-+$/, "");
}

// Mirrors the backend project_slug / team_slug naming rule (core/naming.py):
// lowercase, digits, dashes; must start with a letter and not end with a
// dash; ≤40 chars. Kept in lockstep with the server so the inline indicator
// and the mutation agree on what "valid" means.
const SLUG_RE = /^[a-z]([a-z0-9-]*[a-z0-9])?$/;
export const SLUG_MAX = 40;
export const SLUG_INPUT_PATTERN = "[a-z]([a-z0-9-]*[a-z0-9])?";
export const isValidSlug = (s: string) => SLUG_RE.test(s) && s.length <= SLUG_MAX;

export type SlugStatus = "empty" | "invalid" | "unchanged" | "checking" | "available" | "taken";
