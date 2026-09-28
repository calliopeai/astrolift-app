"use client";

import { LockIcon } from "lucide-react";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ACCENTS, GROUNDS, normalizePartial, type Accent, type Ground } from "@/lib/appearance";
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
 */
export function HouseThemeCard({ org, saving, onSave }: HouseThemeCardProps) {
  const current = React.useMemo(
    () => normalizePartial(org.appearanceDefault),
    [org.appearanceDefault]
  );
  const [ground, setGround] = React.useState<Ground | "">(current.ground ?? "");
  const [accent, setAccent] = React.useState<Accent | "">(current.accent ?? "");
  const [locked, setLocked] = React.useState(Boolean(org.appearanceLocked));

  React.useEffect(() => {
    setGround(current.ground ?? "");
    setAccent(current.accent ?? "");
    setLocked(Boolean(org.appearanceLocked));
  }, [current, org.appearanceLocked]);

  async function save() {
    const appearanceDefault: Record<string, string> = {};
    if (ground) appearanceDefault.ground = ground;
    if (accent) appearanceDefault.accent = accent;
    await onSave(appearanceDefault, locked);
  }

  const dirty =
    ground !== (current.ground ?? "") ||
    accent !== (current.accent ?? "") ||
    locked !== Boolean(org.appearanceLocked);

  return (
    <Card>
      <CardHeader>
        <CardTitle>House theme</CardTitle>
        <CardDescription>
          Applies to anyone who hasn&apos;t picked their own under Settings › Appearance. Leave an
          option on <span className="font-medium">Not set</span> to let people choose it for
          themselves.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
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
          onChange={(v) => setAccent(v as Accent | "")}
          options={(Object.keys(ACCENTS) as Accent[]).map((k) => ({
            value: k,
            label: ACCENTS[k].label,
            swatch: ACCENTS[k].swatch,
          }))}
        />

        <label className="border-border flex cursor-pointer items-start gap-2.5 rounded-lg border p-3">
          <input
            type="checkbox"
            checked={locked}
            onChange={(e) => setLocked(e.target.checked)}
            className="mt-0.5"
          />
          <span>
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

        <div>
          <Button type="button" size="sm" disabled={saving || !dirty} onClick={save}>
            {saving ? "Saving…" : "Save house theme"}
          </Button>
        </div>
      </CardContent>
    </Card>
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
