// Locale set per spec 09 §10. The request loads the selected catalogue only;
// every supported locale needs its own messages, including ICU placeholders.
export const locales = ["en", "es", "fr", "de", "ja", "ko", "zh-Hans", "pt-BR"] as const;
export type Locale = (typeof locales)[number];
export const defaultLocale: Locale = "en";
