"use client";

import * as React from "react";

import { saveUiPrefs, type ServerUiPrefs } from "@/lib/ui-prefs-sync";

/**
 * Per-person display preferences that are neither appearance (lib/appearance)
 * nor visualization (lib/viz-prefs). The person's server preferences are the
 * source of truth when signed in (#2154, lib/ui-prefs-sync.ts); the browser
 * copy is the first paint and the offline fallback.
 *
 * Restricted settings has an organization default (Admin > Organization).
 * The person's own choice wins; with none, the org default applies; with
 * neither known, the product default does.
 */

export const RESTRICTED_SETTINGS = {
  show: {
    label: "Show read-only",
    blurb: "See settings you can't change, disabled, with the permission that would allow it",
  },
  hide: { label: "Hide", blurb: "Only show settings you can change" },
} as const;

export type RestrictedSettings = keyof typeof RESTRICTED_SETTINGS;

export const PRODUCT_RESTRICTED_SETTINGS: RestrictedSettings = "show";

export interface DisplayPrefs {
  /** What applies: settings the viewer lacks the permission for are shown disabled (spec 44 §5.3) or hidden. */
  restrictedSettings: RestrictedSettings;
  /** The person's own choice; null follows the organization. */
  restrictedSettingsChoice: RestrictedSettings | null;
  /** The organization's default, once the server has said; null until then. */
  restrictedSettingsOrgDefault: RestrictedSettings | null;
}

export function isRestrictedSettings(v: unknown): v is RestrictedSettings {
  return typeof v === "string" && v in RESTRICTED_SETTINGS;
}

/** The person's choice, else the org default, else the product default. */
export function resolveRestrictedSettings(
  choice: RestrictedSettings | null,
  orgDefault: RestrictedSettings | null
): RestrictedSettings {
  return choice ?? orgDefault ?? PRODUCT_RESTRICTED_SETTINGS;
}

function displayPrefs(
  choice: RestrictedSettings | null,
  orgDefault: RestrictedSettings | null
): DisplayPrefs {
  return {
    restrictedSettings: resolveRestrictedSettings(choice, orgDefault),
    restrictedSettingsChoice: choice,
    restrictedSettingsOrgDefault: orgDefault,
  };
}

export const DEFAULT_DISPLAY_PREFS: DisplayPrefs = displayPrefs(null, null);

export const STORAGE_KEY = "astrolift.display";

export function parseDisplayPrefs(raw: string | null): DisplayPrefs {
  if (!raw) return DEFAULT_DISPLAY_PREFS;
  try {
    const data = JSON.parse(raw) as Record<string, unknown> | null;
    if (!data || typeof data !== "object") return DEFAULT_DISPLAY_PREFS;
    // Before #2154 only `restrictedSettings` was stored, and only when the
    // person picked one, so a stored value there is their choice.
    const choice =
      "restrictedSettingsChoice" in data ? data.restrictedSettingsChoice : data.restrictedSettings;
    return displayPrefs(
      isRestrictedSettings(choice) ? choice : null,
      isRestrictedSettings(data.restrictedSettingsOrgDefault)
        ? data.restrictedSettingsOrgDefault
        : null
    );
  } catch {
    return DEFAULT_DISPLAY_PREFS;
  }
}

/**
 * The server's answer as display prefs. It replaces the browser copy
 * outright; a value this build does not know reads as unset, so it falls
 * through to the next rule instead of sticking.
 */
export function displayPrefsFromServer(
  server: Pick<ServerUiPrefs, "restrictedSettingsChoice" | "restrictedSettingsOrgDefault">
): DisplayPrefs {
  const choice = server.restrictedSettingsChoice;
  const orgDefault = server.restrictedSettingsOrgDefault;
  return displayPrefs(
    isRestrictedSettings(choice) ? choice : null,
    isRestrictedSettings(orgDefault) ? orgDefault : null
  );
}

const listeners = new Set<() => void>();
let cached: DisplayPrefs | null = null;

function read(): DisplayPrefs {
  if (cached) return cached;
  try {
    cached = parseDisplayPrefs(window.localStorage.getItem(STORAGE_KEY));
  } catch {
    cached = DEFAULT_DISPLAY_PREFS;
  }
  return cached;
}

function write(next: DisplayPrefs) {
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
export function applyServerDisplayPrefs(server: ServerUiPrefs): void {
  write(displayPrefsFromServer(server));
}

/**
 * The prefs and the setter for the person's restricted-settings choice;
 * `null` goes back to following the organization.
 */
export function useDisplayPrefs(): [
  DisplayPrefs,
  (restrictedSettings: RestrictedSettings | null) => void,
] {
  const prefs = React.useSyncExternalStore(subscribe, read, () => DEFAULT_DISPLAY_PREFS);
  const setRestrictedSettings = React.useCallback((choice: RestrictedSettings | null) => {
    write(displayPrefs(choice, read().restrictedSettingsOrgDefault));
    saveUiPrefs({ restrictedSettings: choice });
  }, []);
  return [prefs, setRestrictedSettings];
}
