// Locale set per spec 09 §10. Translation work for the non-English
// locales lands separately — until then their message bundles fall
// back to English keys (next-intl falls back to the default locale on
// missing keys).
export const locales = [
  "en",
  "es",
  "fr",
  "de",
  "ja",
  "ko",
  "zh-Hans",
  "pt-BR",
] as const;
export type Locale = (typeof locales)[number];
export const defaultLocale: Locale = "en";
