"use client";

import * as React from "react";

import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import type { useProfileTimezone } from "./use-profile-timezone";

/**
 * Settings › Profile: the language and timezone cards. Pure: the locale
 * switch comes from useLocaleSwitch, the timezone from useProfileTimezone.
 */
export function ProfilePreferences({
  localeSwitch,
  timezone,
}: {
  localeSwitch: React.ComponentProps<typeof LanguageSwitcher>;
  timezone: ReturnType<typeof useProfileTimezone>;
}) {
  // Theme moved to Settings → Appearance (the full style page); this
  // page keeps the profile-scoped cards only.
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Language</CardTitle>
          <CardDescription>
            Surface language. Falls back to en when a translation is missing.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <LanguageSwitcher {...localeSwitch} />
        </CardContent>
      </Card>

      <TimezoneCard {...timezone} />
    </div>
  );
}

// Radix <SelectItem> forbids an empty-string value (#885). Use a
// non-empty sentinel for the "no override" option and map it to/from
// the empty string `picked` uses for "clear" so persistence is unchanged.
const NO_TZ_OVERRIDE = "__browser__";

function TimezoneCard({
  timezones,
  browserTz,
  savedTz,
  saving,
  save,
}: ReturnType<typeof useProfileTimezone>) {
  // Local picker state: initialised from server value, falls back to
  // browser-detected zone as the visible default (but we store empty
  // string to clear, not the browser zone itself).
  const [picked, setPicked] = React.useState<string>("");

  React.useEffect(() => {
    setPicked(savedTz ?? "");
  }, [savedTz]);

  // "dirty" means the local picker differs from the persisted value.
  const dirty = picked !== (savedTz ?? "");

  async function onSave() {
    if (!dirty) return;
    await save(picked);
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Timezone</CardTitle>
        <CardDescription>
          Override the browser-detected timezone for all timestamps in the dashboard. Leave blank to
          use the zone your browser reports (<span className="text-2xs font-mono">{browserTz}</span>
          ).
        </CardDescription>
      </CardHeader>
      <CardContent className="max-w-sm space-y-3">
        <div className="space-y-2">
          <Label htmlFor="timezone">Timezone</Label>
          <Select
            value={picked || NO_TZ_OVERRIDE}
            onValueChange={(v) => setPicked(v === NO_TZ_OVERRIDE ? "" : v)}
          >
            <SelectTrigger id="timezone">
              <SelectValue placeholder={`${browserTz} (browser-detected)`} />
            </SelectTrigger>
            <SelectContent className="max-h-72">
              <SelectItem value={NO_TZ_OVERRIDE}>
                <span className="text-muted-foreground">
                  {browserTz} (browser-detected, no override)
                </span>
              </SelectItem>
              {timezones.map((tz) => (
                <SelectItem key={tz} value={tz}>
                  {tz}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Button size="sm" disabled={!dirty || saving} onClick={onSave}>
          {saving ? "Saving…" : "Save"}
        </Button>
      </CardContent>
    </Card>
  );
}
