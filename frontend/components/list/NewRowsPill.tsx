"use client";

import { useFormatter, useTranslations } from "next-intl";

import { ArrowUpIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * "3 new ↑" (spec 44 §5.1): live rows have arrived above the ones being
 * read. The list holds still (see `useHeldRows`); this says so, and a click
 * lets them in. Renders nothing at zero. Pure.
 */
export function NewRowsPill({
  count,
  onReveal,
  className,
}: {
  count: number;
  onReveal: () => void;
  className?: string;
}) {
  const t = useTranslations("shared.list");
  const format = useFormatter();
  return (
    <div aria-live="polite" className={cn("flex justify-center", count === 0 && "sr-only")}>
      {count > 0 && (
        <Button
          size="sm"
          variant="secondary"
          onClick={onReveal}
          className={cn("rounded-full shadow-sm", className)}
        >
          {t.rich("newRows", {
            count,
            shown: count > 99 ? "99+" : format.number(count),
            countText: (chunks) => <span className="font-mono tabular-nums">{chunks}</span>,
          })}
          <ArrowUpIcon className="size-3.5" aria-hidden />
        </Button>
      )}
    </div>
  );
}
