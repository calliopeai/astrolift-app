"use client";

import { LockIcon } from "lucide-react";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  ACCENTS,
  GROUNDS,
  MIN_ACCENT_CONTRAST,
  THEMES,
  type Corners,
  type CustomAccent,
  type Density,
  type Ground,
  accentContrast,
  isCustomAccent,
  modeFor,
  parseCustomAccent,
} from "@/lib/appearance";
import { cn } from "@/lib/utils";

import type { useAppearanceSettings } from "./use-appearance-settings";

const DENSITIES: { value: Density; label: string; hint: string }[] = [
  { value: "compact", label: "Compact", hint: "Dense rows with hairline dividers" },
  { value: "cards", label: "Cards", hint: "Boxed items that flow into columns" },
];

const CORNERS: { value: Corners; label: string }[] = [
  { value: 0, label: "Square" },
  { value: 2, label: "2px" },
  { value: 4, label: "4px" },
  { value: 10, label: "Rounded" },
];

/** A miniature of the chrome: sidebar, ground, accent. */
function ThemePreview({ ground, accent }: { ground: string; accent: string }) {
  const dark = modeFor(ground as never) === "dark";
  const grounds: Record<string, [string, string]> = {
    black: ["#000000", "#0a0a0a"],
    charcoal: ["#0e0f10", "#161819"],
    emerald: ["#050b08", "#0a1310"],
    paper: ["#f7f3ea", "#fffdf8"],
    mist: ["#f2f4f7", "#ffffff"],
  };
  const [bg, panel] = grounds[ground] ?? grounds.black;
  const side = dark && ground !== "charcoal" ? "#04231a" : panel;
  return (
    <span
      aria-hidden
      className="border-border flex h-9 overflow-hidden rounded-sm border"
      style={{ background: bg }}
    >
      <span style={{ background: side, width: "26%" }} />
      <span className="flex flex-1 items-center px-1.5">
        <span style={{ background: accent, height: 4, width: "58%", borderRadius: 1 }} />
      </span>
    </span>
  );
}

/**
 * Palette option A: any colour as the accent. It applies only once it stands
 * 3:1 off the current ground; the status colours stay as they are.
 */
function CustomAccentPicker({
  value,
  ground,
  disabled,
  onChange,
}: {
  value: CustomAccent | null;
  ground: Ground;
  disabled: boolean;
  onChange: (accent: CustomAccent) => void;
}) {
  const [draft, setDraft] = React.useState(value ?? "");
  React.useEffect(() => {
    if (value) setDraft(value);
  }, [value]);

  const parsed = parseCustomAccent(draft);
  const check = parsed ? accentContrast(parsed, ground) : null;
  const groundLabel = GROUNDS[ground].label;

  function update(next: string) {
    setDraft(next);
    const hex = parseCustomAccent(next);
    if (hex && accentContrast(hex, ground).ok) onChange(hex);
  }

  return (
    <div className="mt-3 flex min-w-0 flex-col gap-1.5">
      <label htmlFor="custom-accent" className="text-sm font-medium">
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
          id="custom-accent"
          disabled={disabled}
          value={draft}
          placeholder="#2f9e52"
          onChange={(e) => update(e.target.value)}
          aria-invalid={Boolean(draft) && !check?.ok}
          aria-describedby="custom-accent-help"
          className="w-32 font-mono"
        />
        {value && (
          <span className="text-muted-foreground text-xs" aria-live="polite">
            In use
          </span>
        )}
      </div>
      <p
        id="custom-accent-help"
        className={cn(
          "min-w-0 text-xs [overflow-wrap:anywhere]",
          draft && !check?.ok ? "text-danger" : "text-muted-foreground"
        )}
      >
        {!draft ? (
          <>Any colour, as long as it reads against the ground. Status colours never change.</>
        ) : !parsed ? (
          <>
            Use a hex colour, like <span className="font-mono">#2f9e52</span>.
          </>
        ) : check?.ok ? (
          <>
            <span className="font-mono">{check.ratio.toFixed(1)}:1</span> on {groundLabel}. Status
            colours never change.
          </>
        ) : (
          <>
            Too faint on {groundLabel}:{" "}
            <span className="font-mono">{check?.ratio.toFixed(1)}:1</span>, it needs{" "}
            <span className="font-mono">{MIN_ACCENT_CONTRAST}:1</span>. Pick a{" "}
            {modeFor(ground) === "dark" ? "lighter" : "darker"} colour.
          </>
        )}
      </p>
    </div>
  );
}

/** Settings › Appearance. Pure: the preference and its setters come from useAppearanceSettings. */
export function AppearanceSettings({
  appearance,
  locked,
  setAppearance,
  reset,
  chooseTheme,
  chooseGround,
}: ReturnType<typeof useAppearanceSettings>) {
  const activeTheme = THEMES.find(
    (t) => t.ground === appearance.ground && t.accent === appearance.accent
  );

  return (
    <div className="flex flex-col gap-4">
      {locked && (
        <div className="border-info-border bg-info-bg text-info-fg flex items-center gap-2 rounded-lg border px-3 py-2 text-sm">
          <LockIcon className="size-4 shrink-0" />
          <span>Your organization has locked the theme. These controls are read-only.</span>
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Theme</CardTitle>
          <CardDescription>
            Ground and accent. Picking a light theme switches the app to light mode.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(9rem,1fr))] gap-2">
            {THEMES.map((t) => {
              const active = activeTheme?.id === t.id;
              return (
                <button
                  key={t.id}
                  type="button"
                  disabled={locked}
                  aria-pressed={active}
                  onClick={() => chooseTheme(t.ground, t.accent)}
                  className={cn(
                    "border-border hover:border-primary flex flex-col gap-2 rounded-lg border p-2 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-60",
                    active && "border-primary ring-primary ring-1"
                  )}
                >
                  <ThemePreview ground={t.ground} accent={ACCENTS[t.accent].swatch} />
                  <span>
                    <span className="block text-sm font-semibold">{t.name}</span>
                    <span className="text-muted-foreground block text-xs">{t.hint}</span>
                  </span>
                </button>
              );
            })}
          </div>

          <p className="text-muted-foreground text-2xs mt-4 mb-2 font-semibold tracking-[0.08em] uppercase">
            Accent
          </p>
          <div className="flex flex-wrap gap-2">
            {(Object.keys(ACCENTS) as (keyof typeof ACCENTS)[]).map((key) => (
              <button
                key={key}
                type="button"
                disabled={locked}
                aria-pressed={appearance.accent === key}
                onClick={() => setAppearance({ accent: key })}
                className={cn(
                  "border-border hover:border-primary inline-flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-60",
                  appearance.accent === key && "border-primary ring-primary ring-1"
                )}
              >
                <span
                  aria-hidden
                  className="size-3.5 rounded-full"
                  style={{ background: ACCENTS[key].swatch }}
                />
                {ACCENTS[key].label}
              </button>
            ))}
          </div>
          <CustomAccentPicker
            value={isCustomAccent(appearance.accent) ? appearance.accent : null}
            ground={appearance.ground}
            disabled={locked}
            onChange={(accent) => setAppearance({ accent })}
          />

          <p className="text-muted-foreground text-2xs mt-4 mb-2 font-semibold tracking-[0.08em] uppercase">
            Ground
          </p>
          <div className="flex flex-wrap gap-2">
            {(Object.keys(GROUNDS) as (keyof typeof GROUNDS)[]).map((key) => (
              <button
                key={key}
                type="button"
                disabled={locked}
                aria-pressed={appearance.ground === key}
                onClick={() => chooseGround(key)}
                className={cn(
                  "border-border hover:border-primary rounded-md border px-2.5 py-1.5 text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-60",
                  appearance.ground === key && "border-primary ring-primary ring-1"
                )}
              >
                {GROUNDS[key].label}
                <span className="text-muted-foreground ml-1.5 text-xs">{GROUNDS[key].mode}</span>
              </button>
            ))}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Layout</CardTitle>
          <CardDescription>
            How much room lists and panels take, and how sharp their corners are.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div>
            <p className="text-muted-foreground text-2xs mb-2 font-semibold tracking-[0.08em] uppercase">
              Density
            </p>
            <div className="flex flex-wrap gap-2">
              {DENSITIES.map((d) => (
                <button
                  key={d.value}
                  type="button"
                  disabled={locked}
                  aria-pressed={appearance.density === d.value}
                  onClick={() => setAppearance({ density: d.value })}
                  className={cn(
                    "border-border hover:border-primary flex-1 rounded-md border px-3 py-2 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-60",
                    appearance.density === d.value && "border-primary ring-primary ring-1"
                  )}
                >
                  <span className="block text-sm font-semibold">{d.label}</span>
                  <span className="text-muted-foreground block text-xs">{d.hint}</span>
                </button>
              ))}
            </div>
          </div>

          <div>
            <p className="text-muted-foreground text-2xs mb-2 font-semibold tracking-[0.08em] uppercase">
              Corners
            </p>
            <div className="flex flex-wrap gap-2">
              {CORNERS.map((c) => (
                <button
                  key={c.value}
                  type="button"
                  disabled={locked}
                  aria-pressed={appearance.corners === c.value}
                  onClick={() => setAppearance({ corners: c.value })}
                  className={cn(
                    "border-border hover:border-primary border px-3 py-1.5 text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-60",
                    appearance.corners === c.value && "border-primary ring-primary ring-1"
                  )}
                  style={{ borderRadius: c.value }}
                >
                  {c.label}
                </button>
              ))}
            </div>
          </div>
        </CardContent>
      </Card>

      {!locked && (
        <div>
          <Button variant="outline" size="sm" onClick={reset}>
            Reset to defaults
          </Button>
        </div>
      )}
    </div>
  );
}
