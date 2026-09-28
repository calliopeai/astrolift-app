"use client";

import * as React from "react";

import type { AstroliftPermission } from "@/lib/permissions/astrolift-permissions";
import type { ModuleKey } from "@/graphql/user/user.hooks";

export interface PaletteEntry {
  label: string;
  href: string;
  group: string;
  hint?: string;
  /**
   * Server-authoritative `me.modules` gate (spec 36 §1.2) — mirrors the
   * sidebar's module switcher. Entries with no `module` are visible to
   * every authenticated user (Dashboard, Documentation, account pages).
   */
  module?: ModuleKey;
  permission?: AstroliftPermission;
  keywords?: string[];
}

// Module IA (spec 36) — keep the palette in lockstep with the module
// switcher in components/AstroliftNav.tsx: Dashboard / Apps / Agents /
// Workflows / Admin, gated by the server-authoritative `me.modules`
// manifest. Routes off the switcher (the old BUILD / OBSERVE pillars,
// flat RUN extras, and the route-flagged surfaces in
// lib/route-flags.ts) carry no PAGES entry so Cmd-K can't jump to a
// surface the nav has hidden. The pages still EXIST — re-add the entry
// here when the matching flag flips back on.
//
// `SHOW_RECENT_DEPLOYS` mirrors that gate for the dynamic "Recent deploys"
// group (deployments are off-nav in step 1). Flip to `true` alongside the
// nav's runExtras/observe flag to bring the deploy quick-jumps back.

// Quick Actions are imperative shortcuts the operator reaches for
// constantly — they live at the top of the palette regardless of
// query so they're always one Cmd-K away.
export const QUICK_ACTIONS: PaletteEntry[] = [
  {
    label: "Register new app",
    href: "/apps/new",
    group: "Quick Actions",
    hint: "wizard",
    module: "apps",
    permission: "app.create",
    keywords: ["new", "create", "register", "app"],
  },
  {
    label: "Connect a source",
    href: "/providers#source",
    group: "Quick Actions",
    hint: "github / gitlab",
    module: "admin",
    permission: "scm.connect",
    keywords: ["github", "gitlab", "scm", "source", "repo", "oauth"],
  },
  {
    label: "Manage tokens",
    href: "/tokens",
    group: "Quick Actions",
    hint: "API keys",
    module: "admin",
    permission: "api_token.create",
    keywords: ["api", "token", "pat"],
  },
];

// Pages — the static route catalog. Single source of truth for
// everything the palette can navigate to. Keep in sync with
// components/AstroliftNav.tsx; the duplication is small and the
// palette is searched by free-text rather than the hierarchical nav
// structure, so a flat list is the right shape.
export const PAGES: PaletteEntry[] = [
  // The flat module landings + Dashboard.
  { label: "Dashboard", href: "/dashboard", group: "Pages", keywords: ["overview", "home"] },
  { label: "Apps", href: "/apps", group: "Pages", module: "apps" },
  { label: "Agents", href: "/agents", group: "Pages", module: "agents" },
  {
    label: "Workflows",
    href: "/workflows",
    group: "Pages",
    module: "workflows",
    keywords: ["definitions", "runs", "stages"],
  },

  // Admin — Infra.
  {
    label: "Clusters",
    href: "/clusters",
    group: "Pages",
    module: "admin",
    permission: "cluster.update",
  },
  { label: "Domains", href: "/domains", group: "Pages", module: "admin" },
  {
    label: "Providers",
    href: "/providers",
    group: "Pages",
    module: "admin",
    permission: "cluster.update",
  },
  { label: "Webhooks", href: "/webhooks", group: "Pages", module: "admin" },

  // Admin — Settings (org config + RBAC/governance). Destinations match
  // the sidebar's Admin group; /administration/* is canonical (except
  // /tokens, which stays top-level — shipped #893).
  {
    label: "Members",
    href: "/administration/members",
    group: "Pages",
    module: "admin",
    permission: "org.manage_members",
  },
  {
    label: "Teams",
    href: "/administration/teams",
    group: "Pages",
    module: "admin",
    permission: "team.read",
  },
  {
    label: "Projects",
    href: "/administration/projects",
    group: "Pages",
    module: "admin",
    permission: "project.read",
  },
  {
    label: "API Keys",
    href: "/tokens",
    group: "Pages",
    module: "admin",
    permission: "api_token.create",
  },
  {
    label: "Metrics",
    href: "/administration/metrics",
    group: "Pages",
    module: "admin",
    permission: "app.read",
  },
  {
    label: "Audit log",
    href: "/administration/audit",
    group: "Pages",
    module: "admin",
    permission: "audit_log.read",
  },
  { label: "Policies", href: "/administration/policies", group: "Pages", module: "admin" },
  {
    label: "Permissions diagnostics",
    href: "/administration/permissions",
    group: "Pages",
    module: "admin",
    keywords: ["why", "denied", "rbac", "role"],
  },
  {
    label: "Source providers",
    href: "/providers#source",
    group: "Pages",
    module: "admin",
    keywords: ["github", "scm", "git"],
  },
  {
    label: "Identity provider",
    href: "/providers#identity",
    group: "Pages",
    module: "admin",
    keywords: ["sso", "oidc", "saml", "idp"],
  },
  {
    label: "Organization",
    href: "/administration/organization",
    group: "Pages",
    module: "admin",
    permission: "org.update",
  },

  // Always-visible (no module gate): docs + account pages.
  {
    label: "Documentation",
    href: "/documentation",
    group: "Pages",
    keywords: ["docs", "help", "guide", "reference"],
  },
  { label: "Profile", href: "/settings/profile", group: "Pages" },
  { label: "Notifications", href: "/settings/notifications", group: "Pages" },
  { label: "Security", href: "/settings/security", group: "Pages" },
];

// Section ordering for display — Quick Actions on top, then Apps
// (which is dynamic, populated from the org's registered apps), then
// the static Pages catalog. Anything not in this list falls to the
// bottom in insertion order.
const SECTION_ORDER = ["Quick Actions", "Apps", "Pages"];

function matches(entry: PaletteEntry, q: string): boolean {
  if (!q) return true;
  const needle = q.toLowerCase();
  if (entry.label.toLowerCase().includes(needle)) return true;
  if (entry.group.toLowerCase().includes(needle)) return true;
  if (entry.href.toLowerCase().includes(needle)) return true;
  if (entry.keywords?.some((k) => k.toLowerCase().includes(needle))) return true;
  return false;
}

/** Pure (Storybook first): open state and the gated entries come from useCommandPalette. */
export function CommandPalette({
  open,
  onOpenChange: setOpen,
  entries,
  onNavigate,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  entries: PaletteEntry[];
  onNavigate: (href: string) => void;
}) {
  const [query, setQuery] = React.useState("");
  const [active, setActive] = React.useState(0);
  const inputRef = React.useRef<HTMLInputElement>(null);

  React.useEffect(() => {
    if (open) {
      setQuery("");
      setActive(0);
      // Defer to next frame so the input exists in the DOM.
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  const visible = React.useMemo(() => entries.filter((r) => matches(r, query)), [entries, query]);

  const grouped = React.useMemo(() => {
    const out = new Map<string, PaletteEntry[]>();
    for (const r of visible) {
      if (!out.has(r.group)) out.set(r.group, []);
      out.get(r.group)!.push(r);
    }
    // Sort sections by SECTION_ORDER, leaving unknowns at the end in
    // insertion order.
    const known = SECTION_ORDER.filter((g) => out.has(g)).map(
      (g) => [g, out.get(g)!] as [string, PaletteEntry[]]
    );
    const unknown = Array.from(out.entries()).filter(([g]) => !SECTION_ORDER.includes(g));
    return [...known, ...unknown];
  }, [visible]);

  React.useEffect(() => {
    setActive(0);
  }, [query]);

  if (!open) return null;

  function go(href: string) {
    onNavigate(href);
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => Math.min(i + 1, visible.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const target = visible[active];
      if (target) go(target.href);
    }
  }

  let runningIndex = 0;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 px-4 pt-[12vh]"
      onClick={() => setOpen(false)}
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
    >
      <div
        className="bg-popover text-popover-foreground w-full max-w-xl overflow-hidden rounded-lg border shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Type to search routes, apps, or actions…"
          className="w-full border-b bg-transparent px-4 py-3 text-sm outline-none"
          aria-label="Search"
        />
        <div className="max-h-[60vh] overflow-y-auto">
          {grouped.length === 0 ? (
            <p className="text-muted-foreground p-4 text-sm">No matches. Try a different query.</p>
          ) : (
            grouped.map(([group, routes]) => (
              <div key={group} className="py-1">
                <div className="text-muted-foreground text-2xs px-3 py-1 font-semibold tracking-wider uppercase">
                  {group}
                </div>
                <ul>
                  {routes.map((r) => {
                    const idx = runningIndex++;
                    const isActive = idx === active;
                    return (
                      <li key={`${r.group}:${r.href}`}>
                        <button
                          onMouseEnter={() => setActive(idx)}
                          onClick={() => go(r.href)}
                          className={`flex w-full items-center justify-between px-3 py-2 text-sm ${
                            isActive ? "bg-accent text-accent-foreground" : "hover:bg-accent/50"
                          }`}
                        >
                          <span>{r.label}</span>
                          <span className="text-muted-foreground font-mono text-xs">
                            {r.hint ?? r.href}
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))
          )}
        </div>
        <div className="text-muted-foreground text-2xs border-t px-3 py-2">
          ↑↓ navigate · ↵ open · esc close · ⌘K to toggle
        </div>
      </div>
    </div>
  );
}
