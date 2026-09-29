"use client";

import { useQuery } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import * as React from "react";

import { PAGES, type PaletteEntry, QUICK_ACTIONS } from "@/components/CommandPalette";
import { LIST_DEPLOYMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftDeployment } from "@/graphql/lifecycle/lifecycle.types";
import { LIST_APPS } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";
import { useModules } from "@/graphql/user/user.hooks";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

// `SHOW_RECENT_DEPLOYS` mirrors the step-1 IA gate for the dynamic "Recent
// deploys" group (deployments are off-nav), so the query is not even issued.
const SHOW_RECENT_DEPLOYS = false;

interface AppsResp {
  astroliftApps: AstroliftRegisteredApp[];
}

interface DeploymentsResp {
  astroliftDeployments: AstroliftDeployment[];
}

/** ⌘K, and every entry the viewer may reach: the data half of CommandPalette. */
export function useCommandPalette() {
  const [open, setOpen] = React.useState(false);
  const router = useRouter();
  const { can, loading: permsLoading } = useMyPermissions();
  const { canView, loading: modulesLoading } = useModules();

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
      keywords: [a.slug, a.teamSlug, a.projectSlug, a.sourceRepo ?? ""].filter(Boolean),
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
    [appEntries, deploymentEntries]
  );

  const entries = React.useMemo(
    () =>
      allEntries.filter((r) => {
        // Permission-authoritative gating (spec 36): when an entry
        // carries its own fine permission, that permission is the sole
        // gate. The module `canView` manifest only reflects the coarse
        // cluster/org-manage grants, so ANDing it in wrongly hides
        // entries (cost / quota / audit-read) that a role legitimately
        // holds. Module `canView` gates only the permission-less
        // entries — matching the pre-change Can/useMyPermissions
        // semantics.
        if (r.permission) return permsLoading || can(r.permission);
        if (r.module) return modulesLoading || canView(r.module);
        return true;
      }),
    [allEntries, can, permsLoading, canView, modulesLoading]
  );

  return {
    open,
    onOpenChange: setOpen,
    entries,
    onNavigate: (href: string) => {
      setOpen(false);
      router.push(href);
    },
  };
}
