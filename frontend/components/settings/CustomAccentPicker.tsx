"use client";

import { useTranslations } from "next-intl";
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
  const t = useTranslations("appearanceSettings.customAccent");
  const tGround = useTranslations("appearanceSettings.grounds");
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

  const mono = (chunks: React.ReactNode) => <span className="font-mono">{chunks}</span>;
  let help: React.ReactNode;
  if (!draft) {
    help = t("helpEmpty");
  } else if (!parsed) {
    help = t.rich("helpInvalid", { mono });
  } else if (ground && usable) {
    help = t.rich("helpOk", {
      mono,
      ratio: checks[0].ratio.toFixed(1),
      ground: tGround(ground),
    });
  } else if (ground) {
    help = t.rich(modeFor(ground) === "dark" ? "helpTooFaintLighter" : "helpTooFaintDarker", {
      mono,
      ratio: checks[0].ratio.toFixed(1),
      min: MIN_ACCENT_CONTRAST,
      ground: tGround(ground),
    });
  } else if (!usable) {
    help = t.rich("helpFaintEverywhere", { mono, min: MIN_ACCENT_CONTRAST });
  } else if (failing.length === 0) {
    help = t("helpReadsEverywhere");
  } else {
    help = t("helpFaintSome", {
      grounds: failing.map((c) => tGround(c.ground)).join(", "),
      count: failing.length,
    });
  }

  return (
    <div className="mt-3 flex min-w-0 flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium">
        {t("label")}
      </label>
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <input
          type="color"
          aria-label={t("pick")}
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
            {t("inUse")}
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
