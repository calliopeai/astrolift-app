/**
 * Active organization tracking.
 *
 * The user picks an org via the sidebar OrgSwitcher; we persist that
 * selection in a cookie so it survives reloads and so server
 * components can read it. Every GraphQL request mirrors the value as
 * the ``X-Astrolift-Organization`` header (see lib/apollo/links.ts).
 *
 * For the "user is in exactly one org" case the backend resolves the
 * tenant via single-membership and the header is optional. The
 * header makes multi-org users explicit and survives the case where
 * no membership is registered yet (e.g. operators).
 */

const COOKIE_NAME = "astrolift_active_org";
const ONE_YEAR_SECONDS = 365 * 24 * 60 * 60;

export function getActiveOrgGuid(): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie
    .split(";")
    .map((s) => s.trim())
    .find((c) => c.startsWith(`${COOKIE_NAME}=`));
  if (!match) return null;
  return decodeURIComponent(match.split("=")[1] ?? "") || null;
}

export function setActiveOrgGuid(guid: string): void {
  if (typeof document === "undefined") return;
  document.cookie = [
    `${COOKIE_NAME}=${encodeURIComponent(guid)}`,
    "Path=/",
    `Max-Age=${ONE_YEAR_SECONDS}`,
    "SameSite=Lax",
  ].join("; ");
}

export function clearActiveOrgGuid(): void {
  if (typeof document === "undefined") return;
  document.cookie = `${COOKIE_NAME}=; Path=/; Max-Age=0; SameSite=Lax`;
}
