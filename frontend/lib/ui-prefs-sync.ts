"use client";

import * as React from "react";

import type {
  AstroliftUiPreferences,
  UpdateMyUiPreferencesInput,
} from "@/graphql/__generated__/schema";

/**
 * The seam between the browser-stored UI preferences (lib/viz-prefs,
 * lib/display-prefs, lib/home-prefs, lib/appearance) and the person's server
 * preferences (#2154, `astroliftMyUiPreferences` / `updateMyUiPreferences`).
 *
 * Each store keeps its browser copy as the first paint and the offline
 * fallback. When signed in, `UiPreferencesBridge` reads the server and hands
 * the answer to every store, which replaces its browser copy (the server
 * wins), and registers the saver here so a change made on this page is sent
 * to the server as well. Signed out, or before the bridge mounts, there is no
 * saver and a change stays in this browser.
 */

/** What the server answers with, as the stores read it. */
export type ServerUiPrefs = AstroliftUiPreferences;

/** One save: an omitted field is left alone, an explicit null resets it. */
export type UiPrefsPatch = Partial<UpdateMyUiPreferencesInput>;

type Saver = (patch: UiPrefsPatch) => void;

let saver: Saver | null = null;

/** Register the server saver; the returned function unregisters it. */
export function setUiPrefsSaver(next: Saver): () => void {
  saver = next;
  return () => {
    if (saver === next) saver = null;
  };
}

/** Send a change to the server, when there is one to send it to. */
export function saveUiPrefs(patch: UiPrefsPatch): void {
  saver?.(patch);
}

/**
 * Where the server read stands. `loading` holds back anything that must not
 * be decided from a browser copy that may be stale (Home's first-sign-in
 * question); `local` means there is no server answer to wait for (signed
 * out, offline, or the read failed).
 */
export type UiPrefsStatus = "local" | "loading" | "synced";

let status: UiPrefsStatus = "local";
const statusListeners = new Set<() => void>();

export function setUiPrefsStatus(next: UiPrefsStatus): void {
  if (status === next) return;
  status = next;
  statusListeners.forEach((l) => l());
}

function subscribeStatus(listener: () => void) {
  statusListeners.add(listener);
  return () => {
    statusListeners.delete(listener);
  };
}

export function useUiPrefsStatus(): UiPrefsStatus {
  return React.useSyncExternalStore(
    subscribeStatus,
    () => status,
    () => "local"
  );
}
