"use client";

import { useTranslations } from "next-intl";
import type * as React from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { VizLegend, type LegendItem } from "./VizLegend";

/**
 * The frame every switchable viz sits in: a title, the style switcher, the
 * picture, and the legend saying what its motion means. It sets data-motion,
 * which the viz-* classes in globals.css read, so reduced motion is decided
 * once here rather than in each renderer.
 */

export interface VizStyleOption {
  label: string;
  blurb: string;
}

/** A style switcher's options in the viewer's language (`vizRender.styles.<group>.<key>`). */
export function useStyleCopy<K extends string>(
  styles: Record<K, VizStyleOption>,
  group: "fleet" | "workflow" | "app"
): Record<K, VizStyleOption> {
  const t = useTranslations("vizRender");
  return Object.fromEntries(
    (Object.keys(styles) as K[]).map((key) => [
      key,
      {
        label: t(`styles.${group}.${key}.label` as string),
        blurb: t(`styles.${group}.${key}.blurb` as string),
      },
    ])
  ) as Record<K, VizStyleOption>;
}

export interface VizFrameProps<K extends string> {
  title: React.ReactNode;
  description?: React.ReactNode;
  styles: Record<K, VizStyleOption>;
  value: K;
  onChange: (style: K) => void;
  motion: "full" | "reduced";
  legend: LegendItem[];
  /** Extra controls after the switcher (a zoom, a filter). */
  actions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}

export function VizFrame<K extends string>({
  title,
  description,
  styles,
  value,
  onChange,
  motion,
  legend,
  actions,
  children,
  className,
}: VizFrameProps<K>) {
  const t = useTranslations("vizRender");
  return (
    <section data-motion={motion} className={cn("bg-card rounded-md border", className)}>
      <header className="flex flex-wrap items-start justify-between gap-3 border-b px-4 py-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold">{title}</h2>
          {description && <p className="text-muted-foreground mt-0.5 text-xs">{description}</p>}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div
            role="radiogroup"
            aria-label={t("frame.viewStyle")}
            className="bg-muted/50 flex flex-wrap rounded-md p-0.5"
          >
            {(Object.keys(styles) as K[]).map((key) => (
              <Button
                key={key}
                type="button"
                role="radio"
                aria-checked={key === value}
                title={styles[key].blurb}
                size="sm"
                variant={key === value ? "secondary" : "ghost"}
                className="h-7 px-2.5 text-xs"
                onClick={() => onChange(key)}
              >
                {styles[key].label}
              </Button>
            ))}
          </div>
          {actions}
        </div>
      </header>
      <div className="relative min-h-0 overflow-hidden">{children}</div>
      <footer className="border-t px-4 py-2">
        <VizLegend items={legend} motion={motion} />
      </footer>
    </section>
  );
}
