"use client";

/**
 * Nav-bar "Admin elevated for N more minutes" badge (#487) with the
 * one-click deelevate, the documented "log me out of admin" escape hatch.
 * Pure (Storybook first): the state comes from useElevation.
 */

import { ShieldCheckIcon, ShieldOffIcon } from "lucide-react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

export interface ElevationIndicatorProps {
  elevated: boolean;
  secondsRemaining: number;
  deelevating: boolean;
  onDeelevate: () => void;
}

export function ElevationIndicator({
  elevated,
  secondsRemaining,
  deelevating,
  onDeelevate,
}: ElevationIndicatorProps) {
  const t = useTranslations("stepUp");
  if (!elevated) return null;

  const minutes = Math.max(1, Math.round(secondsRemaining / 60));

  return (
    <div
      className="bg-primary/10 text-primary inline-flex items-center gap-2 rounded-md border border-current/20 px-2 py-1 text-xs font-medium"
      role="status"
      aria-live="polite"
      data-testid="elevation-indicator"
    >
      <ShieldCheckIcon className="size-3.5" aria-hidden />
      <span>{t("indicator", { minutes })}</span>
      <Button
        type="button"
        size="sm"
        variant="ghost"
        className="h-6 px-1 py-0 text-xs hover:bg-current/10"
        onClick={onDeelevate}
        disabled={deelevating}
        aria-label={t("deelevateLabel")}
        title={t("deelevateLabel")}
      >
        <ShieldOffIcon className="size-3.5" aria-hidden />
      </Button>
    </div>
  );
}
