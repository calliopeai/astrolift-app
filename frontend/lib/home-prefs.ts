"use client";

import * as React from "react";

import { type HomeLayoutKey, isHomeLayoutKey } from "@/components/home/registry";
import { saveUiPrefs, type ServerUiPrefs } from "@/lib/ui-prefs-sync";

/**
 * The person's Home layout (spec 44 §4.3). It lives on the person's server
 * preferences (#2154, lib/ui-prefs-sync.ts) so it follows them across
 * browsers; the browser copy is the first paint and the offline fallback.
 *
 * `layout` is validated against the registry: an unknown key reads as unset,
 * and unset means "the default from access". `asked` records that the
 * first-sign-in question was answered, so resetting to the default does not
 * bring the question back.
 */

export interface HomePrefs {
  layout: HomeLayoutKey | null;
  asked: boolean;
}

export const DEFAULT_HOME_PREFS: HomePrefs = { layout: null, asked: false };

export const STORAGE_KEY = "astrolift.home";

export function parseHomePrefs(raw: string | null): HomePrefs {
  if (!raw) return DEFAULT_HOME_PREFS;
  try {
    const data = JSON.parse(raw) as Record<string, unknown> | null;
    const layout = isHomeLayoutKey(data?.layout) ? data.layout : null;
    return { layout, asked: data?.asked === true || layout !== null };
  } catch {
    return DEFAULT_HOME_PREFS;
  }
}

/**
 * The server's answer as home prefs. It replaces the browser copy outright;
 * a layout this build does not know reads as unset.
 */
export function homePrefsFromServer(
  server: Pick<ServerUiPrefs, "homeLayout" | "homeLayoutAsked">
): HomePrefs {
  const layout = isHomeLayoutKey(server.homeLayout) ? server.homeLayout : null;
  return { layout, asked: server.homeLayoutAsked || layout !== null };
}

const listeners = new Set<() => void>();
let cached: HomePrefs | null = null;

function read(): HomePrefs {
  if (cached) return cached;
  try {
    cached = parseHomePrefs(window.localStorage.getItem(STORAGE_KEY));
  } catch {
    cached = DEFAULT_HOME_PREFS;
  }
  return cached;
}

function write(next: HomePrefs) {
  cached = next;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    // Private mode or blocked storage: the choice lasts for this page only.
  }
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  const onStorage = (e: StorageEvent) => {
    if (e.key !== STORAGE_KEY) return;
    cached = null;
    listener();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

/** Take the server's answer as this page's prefs, without sending it back. */
export function applyServerHomePrefs(server: ServerUiPrefs): void {
  write(homePrefsFromServer(server));
}

/**
 * The saved prefs and a setter. `setLayout(key)` saves a choice (and marks
 * the question answered); `setLayout(null)` goes back to the access default.
 */
export function useHomePrefs(): [HomePrefs, (layout: HomeLayoutKey | null) => void] {
  const prefs = React.useSyncExternalStore(subscribe, read, () => DEFAULT_HOME_PREFS);
  const setLayout = React.useCallback((layout: HomeLayoutKey | null) => {
    write({ layout, asked: true });
    saveUiPrefs({ homeLayout: layout, homeLayoutAsked: true });
  }, []);
  return [prefs, setLayout];
}
