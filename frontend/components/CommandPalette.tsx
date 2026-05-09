"use client";

import { useRouter } from "next/navigation";
import * as React from "react";

import { useMyPermissions } from "@/lib/permissions/use-my-permissions";
import type { AstroliftPermission } from "@/lib/permissions/astrolift-permissions";

interface PaletteRoute {
  label: string;
  href: string;
  group: string;
  permission?: AstroliftPermission;
  keywords?: string[];
}

// Single source of truth for everything the palette can navigate to.
// Keep in sync with components/AstroliftNav.tsx; the duplication is
// small and the palette is searched by free-text rather than the
// hierarchical nav structure, so a flat list is the right shape.
const ROUTES: PaletteRoute[] = [
  { label: "Overview", href: "/dashboard", group: "Platform" },
  { label: "Apps", href: "/apps", group: "Platform", permission: "app.read" },
  { label: "Projects", href: "/projects", group: "Platform" },
  { label: "Teams", href: "/teams", group: "Platform", permission: "team.read" },

  { label: "Deployments", href: "/deployments", group: "Operations", permission: "app.read" },
  { label: "Environments", href: "/environments", group: "Operations", permission: "app.read" },
  { label: "Workflows", href: "/workflows", group: "Operations", permission: "app.read" },
  { label: "Jobs", href: "/jobs", group: "Operations", permission: "app.read_logs", keywords: ["scheduled", "command", "cron"] },
  { label: "Previews", href: "/previews", group: "Operations", permission: "app.read", keywords: ["pr", "pull request", "ephemeral"] },
  { label: "Events", href: "/events", group: "Operations", permission: "audit_log.read" },
  { label: "Audit log", href: "/audit", group: "Operations", permission: "audit_log.read" },

  { label: "Clusters", href: "/clusters", group: "Infrastructure", permission: "cluster.update" },
  { label: "Domains", href: "/domains", group: "Infrastructure" },
  { label: "Providers", href: "/providers", group: "Infrastructure", permission: "cluster.update" },
  { label: "Webhooks", href: "/webhooks", group: "Infrastructure" },
  { label: "Source providers", href: "/settings/source-providers", group: "Infrastructure", keywords: ["github", "scm", "git"] },

  { label: "Members", href: "/members", group: "Administration", permission: "org.manage_members" },
  { label: "Tokens", href: "/tokens", group: "Administration", permission: "api_token.create" },
  { label: "Cost", href: "/cost", group: "Administration", permission: "billing.read" },
  { label: "Quotas", href: "/quotas", group: "Administration", permission: "billing.read" },
  { label: "Metrics", href: "/metrics", group: "Administration" },

  { label: "Profile", href: "/settings/profile", group: "Settings" },
  { label: "Notifications", href: "/settings/notifications", group: "Settings" },
  { label: "Security", href: "/settings/security", group: "Settings" },
  { label: "Policies", href: "/settings/policies", group: "Settings" },
  { label: "Permissions diagnostics", href: "/settings/permissions", group: "Settings", keywords: ["why", "denied", "rbac", "role"] },
  { label: "Identity provider", href: "/settings/identity-provider", group: "Settings", keywords: ["sso", "oidc", "saml", "idp"] },
  { label: "Organization", href: "/settings/organization", group: "Settings", permission: "org.update" },
];

function matches(route: PaletteRoute, q: string): boolean {
  if (!q) return true;
  const needle = q.toLowerCase();
  if (route.label.toLowerCase().includes(needle)) return true;
  if (route.group.toLowerCase().includes(needle)) return true;
  if (route.href.toLowerCase().includes(needle)) return true;
  if (route.keywords?.some((k) => k.toLowerCase().includes(needle))) return true;
  return false;
}

export function CommandPalette() {
  const [open, setOpen] = React.useState(false);
  const [query, setQuery] = React.useState("");
  const [active, setActive] = React.useState(0);
  const router = useRouter();
  const inputRef = React.useRef<HTMLInputElement>(null);
  const { can, loading: permsLoading } = useMyPermissions();

  // Cmd-K / Ctrl-K toggles the palette. Esc closes via the dialog
  // backdrop click handler. The keydown is attached at window scope
  // so it fires regardless of focus position.
  React.useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((prev) => !prev);
      }
      if (e.key === "Escape") setOpen(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  React.useEffect(() => {
    if (open) {
      setQuery("");
      setActive(0);
      // Defer to next frame so the input exists in the DOM.
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  const visible = React.useMemo(
    () =>
      ROUTES.filter((r) =>
        permsLoading || !r.permission ? true : can(r.permission),
      ).filter((r) => matches(r, query)),
    [query, can, permsLoading],
  );

  // Group routes for display.
  const grouped = React.useMemo(() => {
    const out = new Map<string, PaletteRoute[]>();
    for (const r of visible) {
      if (!out.has(r.group)) out.set(r.group, []);
      out.get(r.group)!.push(r);
    }
    return Array.from(out.entries());
  }, [visible]);

  React.useEffect(() => {
    setActive(0);
  }, [query]);

  if (!open) return null;

  function go(href: string) {
    setOpen(false);
    router.push(href);
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
          placeholder="Type to search routes…"
          className="w-full border-b bg-transparent px-4 py-3 text-sm outline-none"
          aria-label="Search"
        />
        <div className="max-h-[60vh] overflow-y-auto">
          {grouped.length === 0 ? (
            <p className="text-muted-foreground p-4 text-sm">
              No matches. Try a different query.
            </p>
          ) : (
            grouped.map(([group, routes]) => (
              <div key={group} className="py-1">
                <div className="text-muted-foreground px-3 py-1 text-[10px] font-semibold uppercase tracking-wider">
                  {group}
                </div>
                <ul>
                  {routes.map((r) => {
                    const idx = runningIndex++;
                    const isActive = idx === active;
                    return (
                      <li key={r.href}>
                        <button
                          onMouseEnter={() => setActive(idx)}
                          onClick={() => go(r.href)}
                          className={`flex w-full items-center justify-between px-3 py-2 text-sm ${
                            isActive
                              ? "bg-accent text-accent-foreground"
                              : "hover:bg-accent/50"
                          }`}
                        >
                          <span>{r.label}</span>
                          <span className="text-muted-foreground font-mono text-xs">
                            {r.href}
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
        <div className="text-muted-foreground border-t px-3 py-2 text-[10px]">
          ↑↓ navigate · ↵ open · esc close · ⌘K to toggle
        </div>
      </div>
    </div>
  );
}
