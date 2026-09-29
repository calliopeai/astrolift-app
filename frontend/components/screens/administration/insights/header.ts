import type { Crumb } from "@/components/shell/ShellHeader";
import { type NavArea, NAV } from "@/lib/shell/nav-model";

/**
 * `Admin ▾ › <function>` (spec 44 §4.4): the first crumb switches between
 * the Admin area's functions, with the current one checked. Pure, so the
 * stories and the routes draw the same header.
 */
export function adminCrumbs(fnKey: string, label: string, nav: NavArea[] = NAV): Crumb[] {
  const admin = nav.find((a) => a.key === "admin");
  const switcher = (admin?.groups ?? []).flatMap((g) =>
    g.functions.map((f) => ({ label: f.label, href: f.href, active: f.key === fnKey }))
  );
  return [{ label: "Admin", switcher }, { label }];
}
