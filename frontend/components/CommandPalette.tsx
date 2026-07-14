"use client";

import { useQuery } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import * as React from "react";

import { LIST_DEPLOYMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftDeployment } from "@/graphql/lifecycle/lifecycle.types";
import { LIST_APPS } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";
import type { AstroliftPermission } from "@/lib/permissions/astrolift-permissions";

interface PaletteEntry {
  label: string;
  href: string;
  group: string;
  hint?: string;
  permission?: AstroliftPermission;
  keywords?: string[];
}

// IA restructure (spec 31, step 1) — keep the palette in lockstep with
// the sidebar nav in components/AstroliftNav.tsx. Routes whose nav entry
// is flagged OFF there (the BUILD / OBSERVE pillars and the old flat RUN
// extras: Deployments, Environments, Workflows, Jobs, Previews, Events,
// Metrics) are pulled out of PAGES below so Cmd-K can't jump to a surface
// the nav has hidden. The pages still EXIST — re-add the entry here when
// the matching NAV_FLAGS flag flips back on.
//
// `SHOW_RECENT_DEPLOYS` mirrors that gate for the dynamic "Recent deploys"
// group (deployments are off-nav in step 1). Flip to `true` alongside the
// nav's runExtras/observe flag to bring the deploy quick-jumps back.
const SHOW_RECENT_DEPLOYS = false;

// Quick Actions are imperative shortcuts the operator reaches for
// constantly — they live at the top of the palette regardless of
// query so they're always one Cmd-K away.
const QUICK_ACTIONS: PaletteEntry[] = [
  {
    label: "Register new app",
    href: "/apps/new",
    group: "Quick Actions",
    hint: "wizard",
    permission: "app.create",
    keywords: ["new", "create", "register", "app"],
  },
  {
    label: "Connect a source",
    href: "/providers#source",
    group: "Quick Actions",
    hint: "github / gitlab",
    permission: "scm.connect",
    keywords: ["github", "gitlab", "scm", "source", "repo", "oauth"],
  },
  {
    label: "Manage tokens",
    href: "/tokens",
    group: "Quick Actions",
    hint: "API tokens",
    permission: "api_token.create",
    keywords: ["api", "token", "pat"],
  },
];

// Pages — the static route catalog. Single source of truth for
// everything the palette can navigate to. Keep in sync with
// components/AstroliftNav.tsx; the duplication is small and the
// palette is searched by free-text rather than the hierarchical nav
// structure, so a flat list is the right shape.
const PAGES: PaletteEntry[] = [
  // Run & Observe (live primitives) + Dashboard.
  { label: "Dashboard", href: "/dashboard", group: "Pages", keywords: ["overview", "home"] },
  { label: "Apps", href: "/apps", group: "Pages", permission: "app.read" },
  { label: "Agents", href: "/agents", group: "Pages", permission: "app.read" },

  // Control — Infra.
  { label: "Clusters", href: "/clusters", group: "Pages", permission: "cluster.update" },
  { label: "Domains", href: "/domains", group: "Pages" },
  { label: "Providers", href: "/providers", group: "Pages", permission: "cluster.update" },
  { label: "Webhooks", href: "/webhooks", group: "Pages" },

  // Control — Settings (org config + RBAC/governance). Destinations match
  // the sidebar's Settings group; deep settings pages stay reachable here.
  { label: "Members", href: "/administration/members", group: "Pages", permission: "org.manage_members" },
  { label: "Teams", href: "/administration/teams", group: "Pages", permission: "team.read" },
  { label: "Projects", href: "/administration/projects", group: "Pages", permission: "project.read" },
  { label: "Tokens", href: "/tokens", group: "Pages", permission: "api_token.create" },
  { label: "Cost", href: "/administration/cost", group: "Pages", permission: "billing.read" },
  { label: "Quotas", href: "/administration/quotas", group: "Pages", permission: "org.manage_members" },
  { label: "Metrics", href: "/administration/metrics", group: "Pages", permission: "app.read" },
  { label: "Audit log", href: "/audit", group: "Pages", permission: "audit_log.read" },
  { label: "Policies", href: "/settings/policies", group: "Pages" },
  { label: "Permissions diagnostics", href: "/settings/permissions", group: "Pages", keywords: ["why", "denied", "rbac", "role"] },
  { label: "Source providers", href: "/providers#source", group: "Pages", keywords: ["github", "scm", "git"] },
  { label: "Identity provider", href: "/providers#identity", group: "Pages", keywords: ["sso", "oidc", "saml", "idp"] },
  { label: "Organization", href: "/settings/organization", group: "Pages", permission: "org.update" },
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

interface AppsResp {
  astroliftApps: AstroliftRegisteredApp[];
}

interface DeploymentsResp {
  astroliftDeployments: AstroliftDeployment[];
}

export function CommandPalette() {
  const [open, setOpen] = React.useState(false);
  const [query, setQuery] = React.useState("");
  const [active, setActive] = React.useState(0);
  const router = useRouter();
  const inputRef = React.useRef<HTMLInputElement>(null);
  const { can, loading: permsLoading } = useMyPermissions();

  // Lazily load the org's apps so first-load doesn't pay for it. We
  // pre-fetch on first palette open and then keep the cache warm with
  // a network-only re-fetch each subsequent open — the list is small
  // (capped at 5 entries shown) so the cost is negligible.
  const apps = useQuery<AppsResp>(LIST_APPS, { skip: !open });
  // #700 — fetch the 25 most recent deployments lazily so the palette
  // can match by commit SHA / message / image tag / app slug. Same
  // lazy pattern as apps — only fires when the palette is open. Gated by
  // SHOW_RECENT_DEPLOYS for the step-1 IA (deployments are off-nav), so
  // we don't even issue the query while the group is hidden.
  const deployments = useQuery<DeploymentsResp>(LIST_DEPLOYMENTS, {
    variables: { limit: 25 },
    skip: !open || !SHOW_RECENT_DEPLOYS,
  });

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

  // Recent apps: first five of the org's registered apps. The backend
  // sort defaults to (-created_at, name) so newer apps surface first
  // — close enough to "recent activity" for a Cmd-K shortcut without
  // round-tripping through astroliftEvents.
  const appEntries = React.useMemo<PaletteEntry[]>(() => {
    const list = apps.data?.astroliftApps ?? [];
    return list.slice(0, 5).map((a) => ({
      label: a.name,
      href: `/apps/${a.slug}`,
      group: "Apps",
      hint: a.slug,
      keywords: [a.slug, a.teamSlug, a.projectSlug, a.sourceRepo ?? ""].filter(
        Boolean,
      ),
    }));
  }, [apps.data?.astroliftApps]);

  // #700 — recent deployments as palette entries. Searchable by short
  // SHA, full SHA, image tag, commit message, author, app slug. Only
  // shown when the user has typed something — without a query, the
  // palette already has plenty without 25 deploy rows.
  const deploymentEntries = React.useMemo<PaletteEntry[]>(() => {
    const list = deployments.data?.astroliftDeployments ?? [];
    return list.map((d) => {
      const shortSha = (d.commitSha ?? "").slice(0, 7);
      const label = shortSha
        ? `${d.registeredAppSlug} · ${shortSha}${d.commitMessage ? ` — ${d.commitMessage.slice(0, 60)}` : ""}`
        : `${d.registeredAppSlug} · ${d.imageTag || d.id.slice(0, 8)}`;
      return {
        label,
        href: `/deployments/${d.id}`,
        group: "Recent deploys",
        hint: d.status,
        keywords: [
          d.registeredAppSlug,
          d.commitSha ?? "",
          shortSha,
          d.imageTag ?? "",
          d.commitMessage ?? "",
          d.commitAuthor ?? "",
          d.branch ?? "",
        ].filter(Boolean),
      };
    });
  }, [deployments.data?.astroliftDeployments]);

  const allEntries = React.useMemo<PaletteEntry[]>(
    () => [...QUICK_ACTIONS, ...appEntries, ...deploymentEntries, ...PAGES],
    [appEntries, deploymentEntries],
  );

  const visible = React.useMemo(
    () =>
      allEntries
        .filter((r) =>
          permsLoading || !r.permission ? true : can(r.permission),
        )
        .filter((r) => matches(r, query)),
    [allEntries, query, can, permsLoading],
  );

  const grouped = React.useMemo(() => {
    const out = new Map<string, PaletteEntry[]>();
    for (const r of visible) {
      if (!out.has(r.group)) out.set(r.group, []);
      out.get(r.group)!.push(r);
    }
    // Sort sections by SECTION_ORDER, leaving unknowns at the end in
    // insertion order.
    const known = SECTION_ORDER.filter((g) => out.has(g)).map(
      (g) => [g, out.get(g)!] as [string, PaletteEntry[]],
    );
    const unknown = Array.from(out.entries()).filter(
      ([g]) => !SECTION_ORDER.includes(g),
    );
    return [...known, ...unknown];
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
          placeholder="Type to search routes, apps, or actions…"
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
                <div className="text-muted-foreground px-3 py-1 text-2xs font-semibold uppercase tracking-wider">
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
                            isActive
                              ? "bg-accent text-accent-foreground"
                              : "hover:bg-accent/50"
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
        <div className="text-muted-foreground border-t px-3 py-2 text-2xs">
          ↑↓ navigate · ↵ open · esc close · ⌘K to toggle
        </div>
      </div>
    </div>
  );
}
