"use client";

import { useTheme } from "next-themes";

import { type Accent, type Ground, modeFor } from "@/lib/appearance";
import { useDisplayPrefs, type RestrictedSettings } from "@/lib/display-prefs";
import { useAppearance } from "@/providers/AppearanceProvider";

/** Settings › Appearance: the person's appearance preference and its setters. */
export function useAppearanceSettings() {
  const { appearance, locked, setAppearance, reset } = useAppearance();
  const { setTheme } = useTheme();
  const [display, setDisplay] = useDisplayPrefs();

  return {
    appearance,
    locked,
    setAppearance,
    reset,
    restrictedSettings: display.restrictedSettings,
    setRestrictedSettings(restrictedSettings: RestrictedSettings) {
      setDisplay({ restrictedSettings });
    },
    // Choosing a theme also decides light vs dark, so keep next-themes in step.
    chooseTheme(ground: Ground, accent: Accent) {
      setAppearance({ ground, accent });
      setTheme(modeFor(ground));
    },
    chooseGround(ground: Ground) {
      setAppearance({ ground });
      setTheme(modeFor(ground));
    },
  };
}
