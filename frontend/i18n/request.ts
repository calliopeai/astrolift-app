import { getRequestConfig } from "next-intl/server";
import { cookies } from "next/headers";
import { defaultLocale, locales, type Locale } from "./config";

export default getRequestConfig(async () => {
  const cookieStore = await cookies();
  const raw = cookieStore.get("locale")?.value;
  const locale: Locale = locales.includes(raw as Locale) ? (raw as Locale) : defaultLocale;

  // `tz` is written client-side by <TimezoneDetector /> from
  // Intl.DateTimeFormat().resolvedOptions().timeZone. Fall back to UTC
  // before the cookie lands (e.g. first paint, or JS-disabled clients).
  const tz = cookieStore.get("tz")?.value;
  const timeZone = tz ? decodeURIComponent(tz) : "UTC";

  return {
    locale,
    timeZone,
    messages: (await import(`../messages/${locale}.json`)).default,
  };
});
