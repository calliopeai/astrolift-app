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
  payload: unknown
): string | null {
  const record =
    payload !== null && typeof payload === "object" && !Array.isArray(payload)
      ? (payload as Record<string, unknown>)
      : {};
  const kind = (resourceKind ?? "").toLowerCase();
  const id = (resourceId ?? "").trim();
  const appSlug =
    (typeof record.app_slug === "string" && record.app_slug) ||
    (typeof record.slug === "string" && record.slug) ||
    (kind === "app" ? id : "");

  if (!kind && !id) return null;

  if (kind === "app") {
    return appSlug ? `/apps/${encodeURIComponent(appSlug)}` : null;
  }
  if (kind === "cluster") {
    return id ? `/clusters/${encodeURIComponent(id)}` : null;
  }
  if (kind === "workload") {
    const workloadSlug = (typeof record.workload_slug === "string" && record.workload_slug) || id;
    if (appSlug && workloadSlug) {
      return `/apps/${encodeURIComponent(appSlug)}/workloads/${encodeURIComponent(workloadSlug)}`;
    }
    return appSlug ? `/apps/${encodeURIComponent(appSlug)}` : null;
  }
  if (kind === "managed_service" || kind === "managedservice") {
    const msvcId =
      (typeof record.managed_service_id === "string" && record.managed_service_id) || id;
    if (appSlug && msvcId) {
      return `/apps/${encodeURIComponent(appSlug)}/managed-services/${encodeURIComponent(msvcId)}`;
    }
    return msvcId ? `/services` : null;
  }
  return null;
}

export function eventPayloadText(payload: unknown): string | null {
  if (payload == null || (typeof payload === "object" && Object.keys(payload).length === 0)) {
    return null;
  }
  return JSON.stringify(payload) ?? null;
}
