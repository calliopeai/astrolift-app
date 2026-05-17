"use client";

import { ArrowRightIcon, BoxIcon, RocketIcon, ServerIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Popover, PopoverAnchor, PopoverContent, PopoverHeader } from "@/components/ui/popover";

/**
 * data-tour-target attribute that anchors the spotlight to a DOM
 * element. Three stops drive the tour; each one is rendered as a
 * radix Popover anchored to the first match for the selector.
 */
const TOUR_STEPS = [
  {
    key: "apps-tile",
    selector: '[data-onboarding-tour="apps-tile"]',
    icon: RocketIcon,
  },
  {
    key: "apps-nav",
    selector: '[data-onboarding-tour="apps-nav"]',
    icon: BoxIcon,
  },
  {
    key: "clusters-nav",
    selector: '[data-onboarding-tour="clusters-nav"]',
    icon: ServerIcon,
  },
] as const;

type TourStep = (typeof TOUR_STEPS)[number];

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Fired when the operator clicks "Don't show again" or completes
   *  the tour. Parent persists the dismissal in localStorage so the
   *  tour never re-shows. */
  onDismiss: () => void;
}

/**
 * Three-stop guided tour shown right after the operator closes the
 * onboarding wizard. Each stop is a Popover anchored to a DOM
 * element tagged with ``data-onboarding-tour``; the anchor element
 * is looked up live so the tour stays in sync if the layout reflows
 * (sidebar collapse, mobile breakpoint).
 *
 * The current stop becomes a no-op if its anchor isn't on the page
 * (e.g., the user navigated away mid-tour); we transparently advance
 * to the next anchored stop so the tour can't soft-lock.
 */
export function SpotlightTour({ open, onOpenChange, onDismiss }: Props) {
  const t = useTranslations("onboarding.tour");
  const [stepIndex, setStepIndex] = React.useState(0);
  const [anchorRect, setAnchorRect] = React.useState<DOMRect | null>(null);

  // Reset to step 0 each time the tour re-opens. Without this an
  // operator who skipped mid-tour and later replays it via the user
  // menu would resume at the last stop.
  React.useEffect(() => {
    if (open) setStepIndex(0);
  }, [open]);

  // Locate the current step's anchor element. We re-query on every
  // step change and every resize so the popover stays attached if
  // the layout reflows. The fallback advance prevents a soft-lock
  // when a stop's target isn't rendered (e.g., sidebar collapsed).
  React.useEffect(() => {
    if (!open) {
      setAnchorRect(null);
      return;
    }

    function locate() {
      const step: TourStep | undefined = TOUR_STEPS[stepIndex];
      if (step == null) return;
      const el = document.querySelector(step.selector);
      if (el == null) {
        // Anchor missing: try the next stop. If none of the
        // remaining stops resolve, end the tour cleanly.
        for (let i = stepIndex + 1; i < TOUR_STEPS.length; i += 1) {
          if (document.querySelector(TOUR_STEPS[i].selector) != null) {
            setStepIndex(i);
            return;
          }
        }
        onDismiss();
        return;
      }
      setAnchorRect(el.getBoundingClientRect());
    }
    locate();
    window.addEventListener("resize", locate);
    window.addEventListener("scroll", locate, true);
    return () => {
      window.removeEventListener("resize", locate);
      window.removeEventListener("scroll", locate, true);
    };
  }, [open, stepIndex, onDismiss]);

  if (!open || anchorRect == null) return null;

  const step = TOUR_STEPS[stepIndex];
  const isLast = stepIndex === TOUR_STEPS.length - 1;
  const Icon = step.icon;

  return (
    <>
      {/* Dim the rest of the page so attention lands on the popover.
          The cutout uses a static box-shadow halo around the anchor
          rather than an SVG clip-path so it stays sharp on all
          zoom / DPI combos. */}
      <div
        aria-hidden
        className="fixed inset-0 z-40 bg-black/40 transition-opacity"
        onClick={() => onOpenChange(false)}
      />
      <div
        aria-hidden
        className="ring-primary pointer-events-none fixed z-40 rounded-md ring-2 ring-offset-2 transition-all"
        style={{
          top: anchorRect.top - 4,
          left: anchorRect.left - 4,
          width: anchorRect.width + 8,
          height: anchorRect.height + 8,
          boxShadow: "0 0 0 9999px rgba(0,0,0,0.45)",
        }}
      />
      <Popover open={open} onOpenChange={onOpenChange}>
        <PopoverAnchor asChild>
          <div
            aria-hidden
            style={{
              position: "fixed",
              top: anchorRect.top,
              left: anchorRect.left,
              width: anchorRect.width,
              height: anchorRect.height,
              pointerEvents: "none",
            }}
          />
        </PopoverAnchor>
        <PopoverContent
          side="bottom"
          align="center"
          sideOffset={12}
          className="z-50 w-80"
          onOpenAutoFocus={(e) => e.preventDefault()}
        >
          <PopoverHeader>
            <div className="flex items-center gap-2">
              <Icon className="text-primary size-4" />
              <span className="font-medium">{t(`steps.${step.key}.title`)}</span>
            </div>
            <p className="text-muted-foreground text-xs">{t(`steps.${step.key}.description`)}</p>
          </PopoverHeader>
          <div className="text-muted-foreground flex items-center justify-between text-xs">
            <span>{t("progress", { current: stepIndex + 1, total: TOUR_STEPS.length })}</span>
            <button type="button" className="hover:text-foreground underline" onClick={onDismiss}>
              {t("dontShowAgain")}
            </button>
          </div>
          <div className="flex items-center justify-end gap-2">
            <Button type="button" size="sm" variant="ghost" onClick={() => onOpenChange(false)}>
              {t("skipCta")}
            </Button>
            <Button
              type="button"
              size="sm"
              onClick={() => {
                if (isLast) {
                  onDismiss();
                  return;
                }
                setStepIndex((i) => i + 1);
              }}
            >
              {isLast ? t("finishCta") : t("nextCta")}
              {!isLast && <ArrowRightIcon className="size-3.5" />}
            </Button>
          </div>
        </PopoverContent>
      </Popover>
    </>
  );
}
