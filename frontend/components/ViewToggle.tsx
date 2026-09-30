"use client";

import { useTranslations } from "next-intl";

import { LayoutGridIcon, LayoutListIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { ViewMode } from "@/hooks/use-view-toggle";

interface ViewToggleProps {
  mode: ViewMode;
  onChange: (mode: ViewMode) => void;
  className?: string;
}

/**
 * Card / list view toggle. Renders two icon buttons; the active mode
 * is visually highlighted. Sits in the top-right of a list page header
 * alongside sort/filter controls.
 */
export function ViewToggle({ mode, onChange, className }: ViewToggleProps) {
  const t = useTranslations("shared.list");
  return (
    <div className={cn("flex items-center rounded-md border", className)}>
      <Button
        type="button"
        size="icon"
        variant="ghost"
        aria-label={t("cardView")}
        aria-pressed={mode === "card"}
        onClick={() => onChange("card")}
        className={cn(
          "size-8 rounded-l-[calc(theme(borderRadius.md)-1px)] rounded-r-none",
          mode === "card" && "bg-muted text-foreground"
        )}
      >
        <LayoutGridIcon className="size-4" />
      </Button>
      <Button
        type="button"
        size="icon"
        variant="ghost"
        aria-label={t("listView")}
        aria-pressed={mode === "list"}
        onClick={() => onChange("list")}
        className={cn(
          "size-8 rounded-l-none rounded-r-[calc(theme(borderRadius.md)-1px)]",
          mode === "list" && "bg-muted text-foreground"
        )}
      >
        <LayoutListIcon className="size-4" />
      </Button>
    </div>
  );
}
