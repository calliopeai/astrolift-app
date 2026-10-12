import { createTranslator, type AbstractIntlMessages } from "next-intl";

/**
 * A translator over the `vizRender` messages, for the pure layout and
 * summary functions that word what a viz shows. Components pass
 * `useTranslations("vizRender")`; tests pass `vizTranslator(messages)`.
 */
export type VizT = (key: string, values?: Record<string, string | number>) => string;

/** A `vizRender` translator over a catalog, outside React (tests, exports). */
export function vizTranslator(messages: AbstractIntlMessages, locale = "en"): VizT {
  const t = createTranslator({ locale, messages, namespace: "vizRender" });
  return (key, values) => t(key as never, values as never);
}
