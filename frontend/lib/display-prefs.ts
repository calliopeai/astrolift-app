"use client";

import * as React from "react";

/**
 * Per-person display preferences that are neither appearance (lib/appearance)
 * nor visualization (lib/viz-prefs). Stored client-side for the same reason
 * as those: a display choice, with no server-side UI preference store yet.
 */

export const RESTRICTED_SETTINGS = {
  show: {
    label: "Show read-only",
    blurb: "See settings you can't change, disabled, with the permission that would allow it",
  },
  hide: { label: "Hide", blurb: "Only show settings you can change" },
} as const;

export type RestrictedSettings = keyof typeof RESTRICTED_SETTINGS;

export interface DisplayPrefs {
  /** Settings the viewer lacks the permission for: shown disabled (spec 44 §5.3) or hidden. */
  restrictedSettings: RestrictedSettings;
}

export const DEFAULT_DISPLAY_PREFS: DisplayPrefs = { restrictedSettings: "show" };

export const STORAGE_KEY = "astrolift.display";

export function parseDisplayPrefs(raw: string | null): DisplayPrefs {
  if (!raw) return DEFAULT_DISPLAY_PREFS;
  try {
    const data = JSON.parse(raw) as Record<string, unknown> | null;
    const v = data?.restrictedSettings;
    return {
      restrictedSettings:
        typeof v === "string" && v in RESTRICTED_SETTINGS
          ? (v as RestrictedSettings)
          : DEFAULT_DISPLAY_PREFS.restrictedSettings,
    };
  } catch {
    return DEFAULT_DISPLAY_PREFS;
  }
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

export function useDisplayPrefs(): [DisplayPrefs, (patch: Partial<DisplayPrefs>) => void] {
  const prefs = React.useSyncExternalStore(subscribe, read, () => DEFAULT_DISPLAY_PREFS);
  const update = React.useCallback(
    (patch: Partial<DisplayPrefs>) => write({ ...read(), ...patch }),
    []
  );
  return [prefs, update];
}
