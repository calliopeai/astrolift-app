"use client";

import * as React from "react";

import { Input } from "@/components/ui/input";
import {
  GROUNDS,
  MIN_ACCENT_CONTRAST,
  accentContrast,
  modeFor,
  parseCustomAccent,
  type CustomAccent,
  type Ground,
} from "@/lib/appearance";
import { cn } from "@/lib/utils";

export interface CustomAccentPickerProps {
  /** Distinct per page, so two pickers never share a label target. */
  id: string;
  value: CustomAccent | null;
  /**
   * The ground the accent lands on. Null when it varies per person (an org
   * house theme that pins the accent but not the ground): the colour is then
   * checked against every ground, and people on a ground where it is too
   * faint keep the default accent.
   */
  ground: Ground | null;
  disabled?: boolean;
  onChange: (accent: CustomAccent) => void;
}

/**
 * Palette option A: any colour as the accent. It applies only once it stands
 * 3:1 off the ground (WCAG 2.2 §1.4.11); the status colours never change.
 * Used by Settings › Appearance (personal) and the org house theme (default).
 */
export function CustomAccentPicker({
  id,
  value,
  ground,
  disabled,
  onChange,
}: CustomAccentPickerProps) {
  const [draft, setDraft] = React.useState(value ?? "");
  React.useEffect(() => {
    if (value) setDraft(value);
  }, [value]);

  const parsed = parseCustomAccent(draft);
  const grounds = ground ? [ground] : (Object.keys(GROUNDS) as Ground[]);
  const checks = parsed ? grounds.map((g) => ({ ground: g, ...accentContrast(parsed, g) })) : [];
  const passing = checks.filter((c) => c.ok);
  const failing = checks.filter((c) => !c.ok);
  const usable = passing.length > 0;
  const helpId = `${id}-help`;

  function update(next: string) {
    setDraft(next);
    const hex = parseCustomAccent(next);
    if (!hex) return;
    const ok = ground
      ? accentContrast(hex, ground).ok
      : grounds.some((g) => accentContrast(hex, g).ok);
    if (ok) onChange(hex);
  }

  let help: React.ReactNode;
  if (!draft) {
    help = <>Any colour, as long as it reads against the ground. Status colours never change.</>;
  } else if (!parsed) {
    help = (
      <>
        Use a hex colour, like <span className="font-mono">#2f9e52</span>.
      </>
    );
  } else if (ground && usable) {
    help = (
      <>
        <span className="font-mono">{checks[0].ratio.toFixed(1)}:1</span> on {GROUNDS[ground].label}
        . Status colours never change.
      </>
    );
  } else if (ground) {
    help = (
      <>
        Too faint on {GROUNDS[ground].label}:{" "}
        <span className="font-mono">{checks[0].ratio.toFixed(1)}:1</span>, it needs{" "}
        <span className="font-mono">{MIN_ACCENT_CONTRAST}:1</span>. Pick a{" "}
        {modeFor(ground) === "dark" ? "lighter" : "darker"} colour.
      </>
    );
  } else if (!usable) {
    help = (
      <>
        Too faint on every ground; it needs{" "}
        <span className="font-mono">{MIN_ACCENT_CONTRAST}:1</span>.
      </>
    );
  } else if (failing.length === 0) {
    help = <>Reads on every ground. Status colours never change.</>;
  } else {
    help = (
      <>
        Too faint on {failing.map((c) => GROUNDS[c.ground].label).join(", ")}: people on{" "}
        {failing.length === 1 ? "that ground" : "those grounds"} keep the default accent.
      </>
    );
  }

  return (
    <div className="mt-3 flex min-w-0 flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium">
        Custom accent
      </label>
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <input
          type="color"
          aria-label="Pick a custom accent"
          disabled={disabled}
          value={parsed ?? "#2f9e52"}
          onChange={(e) => update(e.target.value)}
          className="border-border size-8 shrink-0 cursor-pointer rounded-sm border bg-transparent disabled:cursor-not-allowed"
        />
        <Input
          id={id}
          disabled={disabled}
          value={draft}
          placeholder="#2f9e52"
          onChange={(e) => update(e.target.value)}
          aria-invalid={Boolean(draft) && !usable}
          aria-describedby={helpId}
          className="w-32 font-mono"
        />
        {value && (
          <span className="text-muted-foreground text-xs" aria-live="polite">
            In use
          </span>
        )}
      </div>
      <p
        id={helpId}
        className={cn(
          "min-w-0 text-xs [overflow-wrap:anywhere]",
          draft && !usable ? "text-danger" : "text-muted-foreground"
        )}
      >
        {help}
      </p>
    </div>
  );
}
