"use client";

import { useEffect } from "react";

/**
 * Writes the browser-detected IANA timezone to a `tz` cookie so the
 * next-intl server config (frontend/i18n/request.ts) can use it as the
 * default display timezone. Renders nothing.
 *
 * Mount once near the root of the authenticated app shell. Re-running
 * on every page load is intentional: cheap (no network), and re-asserts
 * the cookie after the year-long max-age refresh window slips.
 */
export function TimezoneDetector() {
  useEffect(() => {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (!tz) return;
    document.cookie = `tz=${encodeURIComponent(tz)}; path=/; max-age=31536000; SameSite=Lax`;
  }, []);
  return null;
}
