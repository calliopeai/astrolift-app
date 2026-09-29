"use client";

import { useMotion, useVizPrefs } from "@/lib/viz-prefs";

/**
 * Settings › Visualizations: the person's viz preferences, the setter, and
 * the motion those preferences resolve to on this device (for the previews).
 */
export function useVisualizations() {
  const [value, onChange] = useVizPrefs();
  const motion = useMotion();
  return { value, onChange, motion };
}
