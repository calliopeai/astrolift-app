import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import type messages from "@/messages/en.json";

export type RouteTitleKey = keyof (typeof messages)["routeTitles"];

/**
 * A route's `generateMetadata` (#2381): the tab title in the viewer's
 * language, the page first, then where it sits, then the product, so
 * `routeTitle("security", "settings")` reads "Security · Settings ·
 * Astrolift" in English. The title is absolute: the root layout's template
 * would otherwise add the product name a second time.
 */
export function routeTitle(...trail: RouteTitleKey[]): () => Promise<Metadata> {
  return async () => {
    const t = await getTranslations("routeTitles");
    return { title: { absolute: [...trail.map((key) => t(key)), "Astrolift"].join(" · ") } };
  };
}
