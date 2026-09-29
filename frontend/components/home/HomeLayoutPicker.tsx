"use client";

/**
 * The one Home question (spec 44 §4.3): which layout, from the ones the
 * person is offered, each with its sentence and its panels. Used by the
 * first-sign-in card on Home and by Account › Home. Native radios, so
 * arrow keys move the choice and a screen reader reads it as one group.
 * Pure: the value and the save are the caller's.
 */

import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

import { HOME_PANELS, type HomeLayoutDef, type HomeLayoutKey } from "./registry";

export interface HomeLayoutPickerProps {
  /** The offered layouts, in registry order. */
  layouts: HomeLayoutDef[];
  value: HomeLayoutKey | null;
  onChange: (key: HomeLayoutKey) => void;
  /** The layout preselected from access, marked "Suggested". */
  defaultLayout?: HomeLayoutKey | null;
  /** The group's name for assistive tech (the question, or "Home layout"). */
  legend: string;
  /** Show the legend as a visible heading. */
  showLegend?: boolean;
  disabled?: boolean;
  className?: string;
}

export function HomeLayoutPicker({
  layouts,
  value,
  onChange,
  defaultLayout,
  legend,
  showLegend = false,
  disabled = false,
  className,
}: HomeLayoutPickerProps) {
  const name = React.useId();
  return (
    <fieldset disabled={disabled} className={cn("min-w-0", className)}>
      <legend className={showLegend ? "mb-3 text-base font-semibold" : "sr-only"}>{legend}</legend>
      <div className="grid min-w-0 gap-2 md:grid-cols-2">
        {layouts.map((layout) => (
          <label
            key={layout.key}
            className={cn(
              "border-border hover:border-primary flex min-w-0 cursor-pointer gap-3 rounded-md border p-3 transition-colors",
              "has-[:checked]:border-primary has-[:checked]:ring-primary has-[:checked]:ring-1",
              "has-[:focus-visible]:ring-ring has-[:focus-visible]:ring-2",
              "has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-60"
            )}
          >
            <input
              type="radio"
              name={name}
              value={layout.key}
              checked={value === layout.key}
              onChange={() => onChange(layout.key)}
              className="accent-primary mt-1 size-4 shrink-0"
            />
            <span className="min-w-0 flex-1">
              <span className="flex min-w-0 flex-wrap items-center gap-2">
                <span className="text-sm font-semibold">{layout.title}</span>
                {defaultLayout === layout.key && (
                  <Badge variant="outline" className="text-2xs">
                    Suggested
                  </Badge>
                )}
              </span>
              <span className="text-muted-foreground block text-xs [overflow-wrap:anywhere]">
                {layout.description}
              </span>
              <span className="text-muted-foreground/80 text-2xs mt-1 block [overflow-wrap:anywhere]">
                {layout.panels.map((k) => HOME_PANELS[k].title).join(" · ")}
              </span>
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}
