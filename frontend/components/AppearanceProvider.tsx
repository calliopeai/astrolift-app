"use client";

import * as React from "react";

import {
  type Appearance,
  type OrgAppearancePolicy,
  DEFAULT_APPEARANCE,
  applyAppearance,
  readAppearance,
  resolveAppearance,
  writeAppearance,
} from "@/lib/appearance";

interface AppearanceContextValue {
  appearance: Appearance;
  /** True when an org policy locks the theme — the picker renders read-only. */
  locked: boolean;
  setAppearance: (patch: Partial<Appearance>) => void;
  reset: () => void;
}

const AppearanceContext = React.createContext<AppearanceContextValue | null>(null);

export function AppearanceProvider({
  children,
  policy = null,
}: {
  children: React.ReactNode;
  /** Org-level default / lock. Null until #135 serves it. */
  policy?: OrgAppearancePolicy | null;
}) {
  // Start from the shipped default so server and first client render agree;
  // the real preference is read in an effect. The inline script in the root
  // layout has already stamped <html>, so the user never sees the default.
  const [personal, setPersonal] = React.useState<Appearance>(DEFAULT_APPEARANCE);
  const [hydrated, setHydrated] = React.useState(false);

  React.useEffect(() => {
    setPersonal(readAppearance());
    setHydrated(true);
  }, []);

  const { value, locked } = React.useMemo(
    () => resolveAppearance(hydrated ? personal : null, policy),
    [hydrated, personal, policy],
  );

  React.useEffect(() => {
    if (hydrated) applyAppearance(value);
  }, [hydrated, value]);

  const setAppearance = React.useCallback(
    (patch: Partial<Appearance>) => {
      if (locked) return;
      setPersonal((prev) => {
        const next = { ...prev, ...patch };
        writeAppearance(next);
        return next;
      });
    },
    [locked],
  );

  const reset = React.useCallback(() => {
    if (locked) return;
    setPersonal(DEFAULT_APPEARANCE);
    writeAppearance(DEFAULT_APPEARANCE);
  }, [locked]);

  const ctx = React.useMemo(
    () => ({ appearance: value, locked, setAppearance, reset }),
    [value, locked, setAppearance, reset],
  );

  return <AppearanceContext.Provider value={ctx}>{children}</AppearanceContext.Provider>;
}

export function useAppearance(): AppearanceContextValue {
  const ctx = React.useContext(AppearanceContext);
  if (!ctx) throw new Error("useAppearance must be used inside <AppearanceProvider>");
  return ctx;
}
