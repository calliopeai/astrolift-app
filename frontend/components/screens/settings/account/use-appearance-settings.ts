"use client";

import { useTheme } from "next-themes";

import { type Accent, type Ground, modeFor } from "@/lib/appearance";
import { useDisplayPrefs, type RestrictedSettings } from "@/lib/display-prefs";
import { useAppearance } from "@/providers/AppearanceProvider";

/** Settings › Appearance: the person's appearance preference and its setters. */
export function useAppearanceSettings() {
  const { appearance, locked, setAppearance, reset } = useAppearance();
  const { setTheme } = useTheme();
  const [display, setRestrictedChoice] = useDisplayPrefs();

  return {
    appearance,
    locked,
    setAppearance,
    reset,
    /** What applies: the person's choice, else the org default. */
    restrictedSettings: display.restrictedSettings,
    /** The person's own choice; null follows the organization. */
    restrictedSettingsChoice: display.restrictedSettingsChoice,
    /** The organization's default (Admin › Organization), once known. */
    restrictedSettingsOrgDefault: display.restrictedSettingsOrgDefault,
    /** Choose show or hide; `null` goes back to the organization's default. */
    setRestrictedSettings(restrictedSettings: RestrictedSettings | null) {
      setRestrictedChoice(restrictedSettings);
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
