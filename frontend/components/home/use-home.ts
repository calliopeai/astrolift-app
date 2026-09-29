"use client";

import * as React from "react";

import { type ModuleKey, useModules } from "@/graphql/user/user.hooks";
import { useHomePrefs } from "@/lib/home-prefs";
import { useUiPrefsStatus } from "@/lib/ui-prefs-sync";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import {
  defaultLayoutFor,
  type HomeAccess,
  type HomeLayoutKey,
  offeredLayouts,
  resolveLayout,
  visiblePanels,
} from "./registry";

const MODULE_KEYS: ModuleKey[] = ["apps", "agents", "workflows", "admin"];

const noopSubscribe = () => () => {};

/**
 * The viewer's access as Home reads it, from the same cached `me.modules`
 * and `astroliftMyPermissions` the shell already fetched: no query of its own.
 */
export function useHomeAccess(): { access: HomeAccess; loading: boolean } {
  const modules = useModules();
  const permissions = useMyPermissions();
  const { granted } = permissions;
  const viewable = MODULE_KEYS.filter((k) => modules.canView(k)).join(",");
  const access = React.useMemo<HomeAccess>(
    () => ({
      modules: new Set(viewable ? (viewable.split(",") as ModuleKey[]) : []),
      permissions: granted,
    }),
    [viewable, granted]
  );
  // A background refetch with answers in hand is not loading.
  const loading =
    (modules.loading && modules.modules.size === 0) || (permissions.loading && granted.size === 0);
  return { access, loading };
}

/**
 * Everything HomeScreen and Account › Home need: the offered layouts, the one
 * drawn, its visible panels, and the save. The saved layout is on the
 * person's server preferences, with a browser copy (lib/home-prefs.ts).
 */
export function useHome() {
  const { access, loading } = useHomeAccess();
  const [prefs, setLayout] = useHomePrefs();
  // A browser whose copy has no answer waits for the server before asking the
  // first-sign-in question, which another browser may already have answered.
  const awaitingServer = useUiPrefsStatus() === "loading" && !prefs.asked;
  // The saved layout is only known after hydration; until then Home shows its
  // skeleton rather than flashing the first-sign-in question.
  const hydrated = React.useSyncExternalStore(
    noopSubscribe,
    () => true,
    () => false
  );
  const layouts = offeredLayouts(access);
  const layout = resolveLayout(prefs.layout, access);
  return {
    loading: loading || !hydrated || awaitingServer,
    layout,
    layouts,
    panels: layout ? visiblePanels(layout, access) : [],
    defaultLayout: defaultLayoutFor(access),
    savedLayout: prefs.layout,
    // One layout offered is no question to ask.
    firstSignIn: !prefs.asked && layouts.length > 1,
    onLayoutChange: (key: HomeLayoutKey) => setLayout(key),
    onResetLayout: () => setLayout(null),
  };
}
