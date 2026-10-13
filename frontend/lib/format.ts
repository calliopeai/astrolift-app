import { useFormatter, useTranslations } from "next-intl";

/**
 * Formats an ISO timestamp as a compact relative age in the viewer's
 * language (`5m ago` / `3h ago` / `2d ago` in English, `relativeAge.*` in
 * the catalog). Falls back to the date once the delta exceeds 30 days so
 * the cue stays useful for dormant rows. The formatter returns an empty
 * string when the input does not parse; callers should guard on the source
 * value separately.
 */
export function useRelativeAge(): (iso: string) => string {
  const t = useTranslations("relativeAge");
  const format = useFormatter();
  return (iso) => {
    const then = Date.parse(iso);
    if (Number.isNaN(then)) return "";
    const seconds = Math.max(0, (Date.now() - then) / 1000);
    if (seconds < 60) return t("justNow");
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return t("minutes", { count: minutes });
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return t("hours", { count: hours });
    const days = Math.floor(hours / 24);
    if (days < 30) return t("days", { count: days });
    return format.dateTime(new Date(then), { month: "short", day: "numeric", year: "numeric" });
  };
}
