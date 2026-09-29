/**
 * The Apps area's roll-ups (spec 44 §4.1, §4.4): Deployments, Scheduled
 * jobs, Environments and Previews. Pure pieces their lists and details
 * share: the breadcrumbs and the "Today" and "since" windows.
 *
 * Crumbs follow §4.4 rule 2. A list starts at the area, `Apps ▾ ›
 * Deployments`; a detail starts at its function, `Deployments ▾ ›
 * storefront › deploy 4f2a9c1e`. Either way the first crumb is the switcher
 * over the area's functions, with this function checked (rule 3).
 */
import { SINCE_OPTIONS, sinceToIso } from "@/components/screens/administration/insights/audit-list";
import type { Crumb, CrumbSwitchOption } from "@/components/shell/ShellHeader";
import { NAV } from "@/lib/shell/nav-model";

export type AppsFunction = "apps" | "deployments" | "jobs" | "environments" | "previews";

function appsArea() {
  return NAV.find((a) => a.key === "apps");
}

/** The Apps area's functions, `active` checked. */
export function appsSwitcher(active: AppsFunction): CrumbSwitchOption[] {
  return (appsArea()?.groups ?? []).flatMap((g) =>
    g.functions.map((f) => ({ label: f.label, href: f.href, active: f.key === active }))
  );
}

function functionLabel(fn: AppsFunction): string {
  const found = (appsArea()?.groups ?? []).flatMap((g) => g.functions).find((f) => f.key === fn);
  return found?.label ?? fn;
}

/** A roll-up list: `Apps ▾ › Deployments`, or deeper for a list under it. */
export function appsListCrumbs(fn: AppsFunction, ...rest: Crumb[]): Crumb[] {
  const label = functionLabel(fn);
  const href = appsSwitcher(fn).find((o) => o.active)?.href;
  return [
    { label: appsArea()?.label ?? "Apps", switcher: appsSwitcher(fn) },
    rest.length > 0 ? { label, href } : { label },
    ...rest,
  ];
}

/** A detail under a roll-up: `Deployments ▾ › storefront › deploy 4f2a9c1e`. */
export function appsDetailCrumbs(fn: AppsFunction, ...rest: Crumb[]): Crumb[] {
  return [{ label: functionLabel(fn), switcher: appsSwitcher(fn) }, ...rest];
}

/** The since filter's choices, with Today first (the Deployments view). */
export const SINCE_WITH_TODAY = [{ value: "today", label: "Today" }, ...SINCE_OPTIONS];

/**
 * A `since` value as an ISO instant: `today` is the viewer's local
 * midnight, the rest are the audit list's (`24h`, `7d`, `yyyy-mm-dd`).
 */
export function sinceIso(value: string | undefined, now: number): string | null {
  if (value === "today") {
    const d = new Date(now);
    d.setHours(0, 0, 0, 0);
    return d.toISOString();
  }
  return sinceToIso(value, now);
}
