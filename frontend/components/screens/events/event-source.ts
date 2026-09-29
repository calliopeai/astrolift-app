/**
 * Resolve an event's source to a deep link badge (#434 scope B).
 *
 * Mapping:
 *   - app           → /apps/<slug>
 *   - cluster       → /clusters/<id>
 *   - workload      → /apps/<slug>/workloads/<workloadSlug>
 *   - managed_service → /apps/<slug>/managed-services/<id>
 *   - everything else → text-only badge, no link.
 *
 * App-shaped events stamp resource_id with the slug today; payloads
 * may also carry ``app_slug`` (sometimes both, in which case the
 * payload wins because it's the more authoritative source).
 */
export function resolveSourceHref(
  resourceKind: string,
  resourceId: string,
  payload: Record<string, unknown>
): string | null {
  const kind = (resourceKind ?? "").toLowerCase();
  const id = (resourceId ?? "").trim();
  const appSlug =
    (typeof payload.app_slug === "string" && payload.app_slug) ||
    (typeof payload.slug === "string" && payload.slug) ||
    (kind === "app" ? id : "");

  if (!kind && !id) return null;

  if (kind === "app") {
    return appSlug ? `/apps/${encodeURIComponent(appSlug)}` : null;
  }
  if (kind === "cluster") {
    return id ? `/clusters/${encodeURIComponent(id)}` : null;
  }
  if (kind === "workload") {
    const workloadSlug = (typeof payload.workload_slug === "string" && payload.workload_slug) || id;
    if (appSlug && workloadSlug) {
      return `/apps/${encodeURIComponent(appSlug)}/workloads/${encodeURIComponent(workloadSlug)}`;
    }
    return appSlug ? `/apps/${encodeURIComponent(appSlug)}` : null;
  }
  if (kind === "managed_service" || kind === "managedservice") {
    const msvcId =
      (typeof payload.managed_service_id === "string" && payload.managed_service_id) || id;
    if (appSlug && msvcId) {
      return `/apps/${encodeURIComponent(appSlug)}/managed-services/${encodeURIComponent(msvcId)}`;
    }
    return msvcId ? `/services` : null;
  }
  return null;
}
