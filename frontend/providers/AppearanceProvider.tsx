"use client";

import * as React from "react";

import {
  type Appearance,
  type OrgAppearancePolicy,
  clearAppearance,
  applyAppearance,
  readAppearance,
  resolveAppearance,
  writeAppearance,
} from "@/lib/appearance";
import { saveUiPrefs } from "@/lib/ui-prefs-sync";

interface AppearanceContextValue {
  appearance: Appearance;
  /** True when an org policy locks the theme — the picker renders read-only. */
  locked: boolean;
  setAppearance: (patch: Partial<Appearance>) => void;
  reset: () => void;
  /**
   * Supply the org policy once it is known. Called by
   * `AppearancePolicyBridge` from inside the authenticated layout — this
   * provider sits in the root layout, which also wraps the login pages where
   * no org query can run.
   */
  setPolicy: (policy: OrgAppearancePolicy | null) => void;
  /**
   * Take the person's appearance from their server preferences (#2154),
   * replacing the browser copy without sending it back. Called by
   * `UiPreferencesBridge`; `null` means they have never chosen.
   */
  applyServerAppearance: (personal: Partial<Appearance> | null) => void;
}

const AppearanceContext = React.createContext<AppearanceContextValue | null>(null);

export function AppearanceProvider({
  children,
  policy = null,
}: {
  children: React.ReactNode;
  /** Initial org policy, when a caller already has it. */
  policy?: OrgAppearancePolicy | null;
}) {
  // null until read, and null again when this person has never chosen — the
  // org default only applies to people with no preference of their own, so
  // "unset" has to be representable. The inline script in the root layout has
  // already stamped <html>, so nobody sees the pre-hydration state.
  const [personal, setPersonal] = React.useState<Partial<Appearance> | null>(null);
  const [hydrated, setHydrated] = React.useState(false);
  const [orgPolicy, setOrgPolicy] = React.useState<OrgAppearancePolicy | null>(policy);
  // The same value as `personal`, readable by the setters without a stale closure.
  const personalRef = React.useRef<Partial<Appearance> | null>(null);

  React.useEffect(() => {
    personalRef.current = readAppearance();
    setPersonal(personalRef.current);
    setHydrated(true);
  }, []);

  const { value, locked } = React.useMemo(
    () => resolveAppearance(hydrated ? personal : null, orgPolicy),
    [hydrated, personal, orgPolicy]
  );

  React.useEffect(() => {
    if (hydrated) applyAppearance(value);
  }, [hydrated, value]);

  const setAppearance = React.useCallback(
    (patch: Partial<Appearance>) => {
      if (locked) return;
      const next = { ...(personalRef.current ?? {}), ...patch };
      personalRef.current = next;
      setPersonal(next);
      writeAppearance(next);
      saveUiPrefs({ appearance: next as Record<string, unknown> });
    },
    [locked]
  );

  // Reset means "I have no preference", not "my preference is the shipped
  // default" — so it clears storage and hands the decision back to the org.
  const reset = React.useCallback(() => {
    if (locked) return;
    personalRef.current = null;
    setPersonal(null);
    clearAppearance();
    saveUiPrefs({ appearance: null });
  }, [locked]);

  const applyServerAppearance = React.useCallback((next: Partial<Appearance> | null) => {
    personalRef.current = next;
    setPersonal(next);
    setHydrated(true);
    if (next) writeAppearance(next);
    else clearAppearance();
  }, []);

  const setPolicy = React.useCallback((next: OrgAppearancePolicy | null) => {
    setOrgPolicy(next);
  }, []);

  const ctx = React.useMemo(
    () => ({ appearance: value, locked, setAppearance, reset, setPolicy, applyServerAppearance }),
    [value, locked, setAppearance, reset, setPolicy, applyServerAppearance]
  );

  return <AppearanceContext.Provider value={ctx}>{children}</AppearanceContext.Provider>;
}

export function useAppearance(): AppearanceContextValue {
  const ctx = React.useContext(AppearanceContext);
  if (!ctx) throw new Error("useAppearance must be used inside <AppearanceProvider>");
  return ctx;
}
