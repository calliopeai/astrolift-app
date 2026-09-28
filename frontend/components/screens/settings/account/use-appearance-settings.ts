"use client";

import { useTheme } from "next-themes";

import { type Accent, type Ground, modeFor } from "@/lib/appearance";
import { useAppearance } from "@/providers/AppearanceProvider";

/** Settings › Appearance: the person's appearance preference and its setters. */
export function useAppearanceSettings() {
  const { appearance, locked, setAppearance, reset } = useAppearance();
  const { setTheme } = useTheme();

  return {
    appearance,
    locked,
    setAppearance,
    reset,
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
