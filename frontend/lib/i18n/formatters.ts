"use client";

import { useFormatter, useLocale } from "next-intl";

/**
 * Locale-aware Intl formatters with sensible defaults for the dashboard's
 * common cases (short date, USD currency, relative "5m ago"). Wraps
 * next-intl's `useFormatter` so callers don't repeat boilerplate.
 *
 *   const fmt = useFormatters();
 *   fmt.formatDate(deployment.createdAt);            // "Mar 5, 2026"
 *   fmt.formatNumber(deployment.replicas);           // "1,200"
 *   fmt.formatCurrency(invoice.totalCents / 100);    // "$1,234.50"
 *   fmt.formatRelativeTime(event.timestamp);         // "5 minutes ago"
 *
 * For a one-off format that doesn't fit, use `useFormatter()` from
 * next-intl directly — these helpers are deliberately narrow so a
 * page-level change can't regress every other surface.
 */
export function useFormatters() {
  const locale = useLocale();
  const fmt = useFormatter();

  const toDate = (d: Date | string) => (typeof d === "string" ? new Date(d) : d);

  return {
    locale,
    formatDate: (d: Date | string) =>
      fmt.dateTime(toDate(d), { year: "numeric", month: "short", day: "numeric" }),
    formatDateTime: (d: Date | string) =>
      fmt.dateTime(toDate(d), {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "numeric",
        timeZoneName: "short",
      }),
    formatNumber: (n: number) => fmt.number(n),
    formatPercent: (n: number) => fmt.number(n, { style: "percent" }),
    formatCurrency: (amount: number, currency = "USD") =>
      fmt.number(amount, { style: "currency", currency }),
    formatRelativeTime: (d: Date | string, now: Date = new Date()) =>
      fmt.relativeTime(toDate(d), now),
  };
}
