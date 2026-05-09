/**
 * Locale → text direction. Used by app/layout.tsx to set `<html dir>`
 * and by component-level direction-sensitive logic.
 *
 * None of the currently-shipped locales are RTL, but this is the seam
 * for adding ar / he / fa / ur later — extend RTL_LOCALES and the
 * <html dir> attribute flips automatically.
 */

const RTL_LOCALES = new Set<string>([
  // "ar", "he", "fa", "ur" — added when we onboard those audiences.
]);

export function getDirection(locale: string): "ltr" | "rtl" {
  return RTL_LOCALES.has(locale) ? "rtl" : "ltr";
}
