"use client";

import { useLocale, useTranslations } from "next-intl";
import { useFormatters } from "@/lib/i18n/formatters";

import type { ManagedServiceCostPreview } from "./use-project-resources";

/** An explicit preview with evidence; missing or malformed amounts never become zero. */
export function ManagedResourceCost({ preview }: { preview: ManagedServiceCostPreview }) {
  const copy = useTranslations("projectResources.reads");
  const locale = useLocale();
  const fmt = useFormatters();
  const amount = preview.monthlyTotal;
  let formatted: string | null = null;
  const fetched = new Date(preview.pricingFetchedAt);
  if (
    preview.available &&
    typeof amount === "number" &&
    Number.isFinite(amount) &&
    amount >= 0 &&
    preview.currency &&
    preview.pricingSourceUrl &&
    Number.isFinite(fetched.getTime())
  ) {
    try {
      formatted = new Intl.NumberFormat(locale, {
        style: "currency",
        currency: preview.currency,
      }).format(amount);
    } catch {
      /* An unsupported currency is unavailable, not a misleading amount. */
    }
  }
  let source: string | null = null;
  try {
    const parsed = new URL(preview.pricingSourceUrl);
    if (parsed.protocol === "https:") source = parsed.href;
  } catch {
    /* Unavailable pricing has no link. */
  }
  if (formatted === null || source === null) {
    return <p className="text-muted-foreground text-sm">{copy("costUnavailable")}</p>;
  }
  return (
    <div className="bg-muted/30 space-y-2 rounded-md border p-3 text-sm">
      <p className="font-medium">
        {copy(preview.approximate ? "costApproximate" : "costMonthly", { amount: formatted })}
      </p>
      <p className="text-muted-foreground text-xs">
        {copy("costFetched", {
          at: fmt.formatDateTime(preview.pricingFetchedAt),
        })}
      </p>
      <a className="underline underline-offset-2" href={source} target="_blank" rel="noreferrer">
        {copy("costSource")}
      </a>
      {preview.notes.map((note, index) => (
        <p key={index} className="text-muted-foreground text-xs">
          {note}
        </p>
      ))}
    </div>
  );
}
