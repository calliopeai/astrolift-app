import {
  BellIcon,
  BuildingIcon,
  FileBoxIcon,
  GitBranchIcon,
  KeyIcon,
  KeyRoundIcon,
  ScaleIcon,
  ShieldCheckIcon,
  ShieldIcon,
  UserCircleIcon,
  Users2Icon,
  UsersIcon,
} from "lucide-react";

/**
 * Single source of truth for the /settings nav tree.
 *
 * Consumed by the persistent sidebar (`layout.tsx`), the landing card
 * grid (`page.tsx`), and the breadcrumb. Adding a sub-route means
 * adding an entry here — no other surface needs to know.
 *
 * `i18nKey` is the trailing segment under `settings.nav.sections.*`
 * for the sidebar item and under `settingsIndex.sections.*` for the
 * landing card.
 *
 * `external: true` marks a link that lives outside the /settings tree
 * (e.g. API tokens at `/tokens`). External entries render in the
 * sidebar but do not get a layout-level breadcrumb match.
 */
export type SettingsSectionKey =
  | "organization"
  | "members"
  | "teams"
  | "projects"
  | "identityProvider"
  | "sourceProviders"
  | "policies"
  | "permissions"
  | "profile"
  | "security"
  | "notifications"
  | "apiTokens";

export interface SettingsNavItem {
  key: SettingsSectionKey;
  href: string;
  icon: typeof BellIcon;
  i18nKey: SettingsSectionKey;
  external?: boolean;
}

export interface SettingsNavGroup {
  key: "organization" | "personal" | "tokens";
  items: SettingsNavItem[];
}

export const SETTINGS_NAV: SettingsNavGroup[] = [
  {
    key: "organization",
    items: [
      {
        key: "organization",
        href: "/settings/organization",
        icon: BuildingIcon,
        i18nKey: "organization",
      },
      {
        key: "members",
        href: "/members",
        icon: Users2Icon,
        i18nKey: "members",
        external: true,
      },
      {
        key: "teams",
        href: "/teams",
        icon: UsersIcon,
        i18nKey: "teams",
        external: true,
      },
      {
        key: "projects",
        href: "/projects",
        icon: FileBoxIcon,
        i18nKey: "projects",
        external: true,
      },
      {
        key: "identityProvider",
        href: "/settings/identity-provider",
        icon: KeyRoundIcon,
        i18nKey: "identityProvider",
      },
      {
        key: "sourceProviders",
        href: "/settings/source-providers",
        icon: GitBranchIcon,
        i18nKey: "sourceProviders",
      },
      {
        key: "policies",
        href: "/settings/policies",
        icon: ScaleIcon,
        i18nKey: "policies",
      },
      {
        key: "permissions",
        href: "/settings/permissions",
        icon: ShieldCheckIcon,
        i18nKey: "permissions",
      },
    ],
  },
  {
    key: "personal",
    items: [
      {
        key: "profile",
        href: "/settings/profile",
        icon: UserCircleIcon,
        i18nKey: "profile",
      },
      {
        key: "security",
        href: "/settings/security",
        icon: ShieldIcon,
        i18nKey: "security",
      },
      {
        key: "notifications",
        href: "/settings/notifications",
        icon: BellIcon,
        i18nKey: "notifications",
      },
    ],
  },
  {
    key: "tokens",
    items: [
      {
        key: "apiTokens",
        href: "/tokens",
        icon: KeyIcon,
        i18nKey: "apiTokens",
        external: true,
      },
    ],
  },
];

/**
 * Flat list of every nav item across all groups. Useful for the
 * landing card grid (which renders without grouping) and the
 * breadcrumb lookup.
 */
export const SETTINGS_NAV_FLAT: SettingsNavItem[] = SETTINGS_NAV.flatMap((g) => g.items);

/**
 * Look up the group key for a given section. Returns null when the
 * section is not in the nav tree (e.g. an unknown path under
 * /settings). Caller renders an unscoped breadcrumb in that case.
 */
export function findSectionGroup(sectionKey: SettingsSectionKey): SettingsNavGroup["key"] | null {
  for (const group of SETTINGS_NAV) {
    if (group.items.some((it) => it.key === sectionKey)) return group.key;
  }
  return null;
}

/**
 * Resolve a sub-route URL path (e.g. ``/settings/identity-provider``)
 * to its nav item. Trailing slashes and deeper paths are tolerated —
 * the longest matching prefix wins so nested routes (if added later)
 * still hit the right section.
 */
export function findNavItemByPath(pathname: string): SettingsNavItem | null {
  let best: SettingsNavItem | null = null;
  for (const item of SETTINGS_NAV_FLAT) {
    if (pathname === item.href || pathname.startsWith(item.href + "/")) {
      if (!best || item.href.length > best.href.length) {
        best = item;
      }
    }
  }
  return best;
}
