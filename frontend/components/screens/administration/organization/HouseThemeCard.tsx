"use client";

import { LockIcon } from "lucide-react";
import * as React from "react";

import { CustomAccentPicker } from "@/components/settings/CustomAccentPicker";
import { SettingsSection } from "@/components/settings/SettingsPage";
import {
  ACCENTS,
  GROUNDS,
  isAccent,
  isCustomAccent,
  normalizePartial,
  type AccentChoice,
  type Ground,
} from "@/lib/appearance";
import { cn } from "@/lib/utils";

import type { useHouseTheme } from "./use-house-theme";

export type HouseThemeCardProps = ReturnType<typeof useHouseTheme>;

/**
 * The org's house theme (#135).
 *
 * Two independent decisions, deliberately not collapsed into one control:
 * *what* the house theme is, and whether people may override it. An org can
 * publish a default it still lets people change — that is the common case, so
 * locking has to be a separate opt-in rather than implied by setting a theme.
 *
 * Axes left unset stay personal. Pinning only the accent leaves ground,
 * density and corners to each person, which is why the payload is a partial
 * and "Not set" is a real choice rather than a placeholder.
 *
 * The accent is a preset or a custom colour (palette option A). The org's
 * accent is the default; anyone who picks their own under Settings ›
 * Appearance overrides it, unless the theme is locked.
 */
export function HouseThemeCard({ org, saving, onSave }: HouseThemeCardProps) {
  const current = React.useMemo(
    () => normalizePartial(org.appearanceDefault),
    [org.appearanceDefault]
  );
  const [ground, setGround] = React.useState<Ground | "">(current.ground ?? "");
  const currentAccent: AccentChoice | "" =
    isAccent(current.accent) || isCustomAccent(current.accent) ? current.accent : "";
  const [accent, setAccent] = React.useState<AccentChoice | "">(currentAccent);
  const [locked, setLocked] = React.useState(Boolean(org.appearanceLocked));

  React.useEffect(() => {
    setGround(current.ground ?? "");
    setAccent(currentAccent);
    setLocked(Boolean(org.appearanceLocked));
  }, [current, currentAccent, org.appearanceLocked]);

  async function save() {
    const appearanceDefault: Record<string, string> = {};
    if (ground) appearanceDefault.ground = ground;
    if (accent) appearanceDefault.accent = accent;
    await onSave(appearanceDefault, locked);
  }

  const dirty =
    ground !== (current.ground ?? "") ||
    accent !== currentAccent ||
    locked !== Boolean(org.appearanceLocked);

  return (
    <SettingsSection
      title="House theme"
      description={
        <>
          Applies to anyone who hasn&apos;t picked their own under Settings › Appearance. Leave an
          option on <span className="font-medium">Not set</span> to let people choose it for
          themselves. People can still pick their own accent there unless the theme is locked.
        </>
      }
      dirty={dirty}
      saving={saving}
      saveLabel="Save house theme"
      onCancel={() => {
        setGround(current.ground ?? "");
        setAccent(currentAccent);
        setLocked(Boolean(org.appearanceLocked));
      }}
      onSave={save}
    >
      <Choice
        label="Ground"
        value={ground}
        onChange={(v) => setGround(v as Ground | "")}
        options={(Object.keys(GROUNDS) as Ground[]).map((k) => ({
          value: k,
          label: GROUNDS[k].label,
          hint: GROUNDS[k].mode,
        }))}
      />
      <Choice
        label="Accent"
        value={accent}
        onChange={(v) => setAccent(v as AccentChoice | "")}
        options={(Object.keys(ACCENTS) as (keyof typeof ACCENTS)[]).map((k) => ({
          value: k,
          label: ACCENTS[k].label,
          swatch: ACCENTS[k].swatch,
        }))}
      />
      <CustomAccentPicker
        id="house-custom-accent"
        value={isCustomAccent(accent) ? accent : null}
        ground={ground || null}
        onChange={(hex) => setAccent(hex)}
      />

      <label className="border-border flex min-w-0 cursor-pointer items-start gap-2.5 rounded-lg border p-3">
        <input
          type="checkbox"
          checked={locked}
          onChange={(e) => setLocked(e.target.checked)}
          className="mt-0.5"
        />
        <span className="min-w-0">
          <span className="flex items-center gap-1.5 text-sm font-semibold">
            <LockIcon aria-hidden className="size-3.5" />
            Lock this theme
          </span>
          <span className="text-muted-foreground block text-xs">
            Everyone gets the house theme and their personal picker becomes read-only. Off by
            default — publishing a default and forcing it are different decisions.
          </span>
        </span>
      </label>
    </SettingsSection>
  );
}

function Choice({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string; hint?: string; swatch?: string }[];
}) {
  return (
    <div>
      <p className="text-muted-foreground text-2xs mb-2 font-semibold tracking-[0.08em] uppercase">
        {label}
      </p>
      <div className="flex flex-wrap gap-2">
        {[{ value: "", label: "Not set" }, ...options].map((o) => (
          <button
            key={o.value || "unset"}
            type="button"
            aria-pressed={value === o.value}
            onClick={() => onChange(o.value)}
            className={cn(
              "border-border hover:border-primary inline-flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-sm transition-colors",
              value === o.value && "border-primary ring-primary ring-1"
            )}
          >
            {o.swatch && (
              <span
                aria-hidden
                className="size-3.5 rounded-full"
                style={{ background: o.swatch }}
              />
            )}
            {o.label}
            {o.hint && <span className="text-muted-foreground text-xs">{o.hint}</span>}
          </button>
        ))}
      </div>
    </div>
  );
}
