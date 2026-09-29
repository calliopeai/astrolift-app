import type { Crumb } from "@/components/shell/ShellHeader";
import { NAV } from "@/lib/shell/nav-model";

/**
 * The first crumb on every Admin › Organization page (spec 44 §4.4 rule 3):
 * `Admin ▾`, switching between the group's functions, with `active` checked.
 * Read from the rail's own model so the crumb and the rail always agree
 * (rule 7).
 */
export function adminCrumb(active: string): Crumb {
  const group = NAV.find((a) => a.key === "admin")?.groups.find((g) =>
    g.functions.some((f) => f.key === active)
  );
  return {
    label: "Admin",
    switcher: (group?.functions ?? []).map((f) => ({
      label: f.label,
      href: f.href,
      active: f.key === active,
    })),
  };
}

export type PermissionsPage = "roles" | "assignments" | "diagnostics";

export const PERMISSIONS_PAGES: ReadonlyArray<{
  key: PermissionsPage;
  label: string;
  href: string;
}> = [
  { key: "roles", label: "Roles", href: "/administration/permissions" },
  { key: "assignments", label: "Assignments", href: "/administration/permissions/assignments" },
  { key: "diagnostics", label: "Diagnostics", href: "/administration/permissions/diagnostics" },
];

/**
 * `Admin ▾ › Permissions ▾ › Roles`. Roles, Assignments and Diagnostics were
 * three tabs on one page; each is a list (or a diagnostics page) with its own
 * header now, so the move between them is the `Permissions ▾` switcher.
 */
export function permissionsCrumbs(page: PermissionsPage): Crumb[] {
  const current = PERMISSIONS_PAGES.find((p) => p.key === page)!;
  return [
    adminCrumb("permissions"),
    {
      label: "Permissions",
      switcher: PERMISSIONS_PAGES.map((p) => ({
        label: p.label,
        href: p.href,
        active: p.key === page,
      })),
    },
    { label: current.label },
  ];
}
