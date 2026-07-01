"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useTheme } from "next-themes";
import * as React from "react";
import { toast } from "sonner";

import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { UPDATE_MY_PROFILE } from "@/graphql/identity/identity.mutations";
import { GET_MY_PROFILE } from "@/graphql/identity/identity.queries";
import type {
  AstroliftMyProfile,
  MutationResult,
} from "@/graphql/identity/identity.types";

interface ProfileResp {
  astroliftMyProfile: AstroliftMyProfile | null;
}
interface MutationResp {
  updateMyProfile: MutationResult<AstroliftMyProfile>;
}

// Build the sorted IANA timezone list once at module load — it's
// 400–600 entries depending on the browser; memoising avoids a
// new array on every render.
const TIMEZONES: string[] = (() => {
  try {
    return [...(Intl as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf!("timeZone")].sort();
  } catch {
    return [];
  }
})();

export function AppearanceClient() {
  const { theme, setTheme } = useTheme();

  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Appearance</CardTitle>
          <CardDescription>
            Choose how the dashboard looks. Defaults to your system preference.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2 max-w-sm">
          <Label htmlFor="theme">Theme</Label>
          <Select value={theme ?? "system"} onValueChange={setTheme}>
            <SelectTrigger id="theme">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="system">System</SelectItem>
              <SelectItem value="light">Light</SelectItem>
              <SelectItem value="dark">Dark</SelectItem>
            </SelectContent>
          </Select>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Language</CardTitle>
          <CardDescription>
            Surface language. Falls back to en when a translation is missing.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <LanguageSwitcher />
        </CardContent>
      </Card>

      <TimezoneCard />
    </div>
  );
}

function TimezoneCard() {
  const { data } = useQuery<ProfileResp>(GET_MY_PROFILE);
  const [updateProfile, { loading: saving }] = useMutation<MutationResp>(
    UPDATE_MY_PROFILE,
    { refetchQueries: [{ query: GET_MY_PROFILE }], awaitRefetchQueries: true },
  );

  const profile = data?.astroliftMyProfile ?? null;

  // Detect the browser's current timezone for the placeholder / default.
  const browserTz = React.useMemo(
    () => Intl.DateTimeFormat().resolvedOptions().timeZone,
    [],
  );

  // savedTz is the persisted server value (null/empty = no override).
  const savedTz = profile?.timezone ?? null;

  // Local picker state: initialised from server value, falls back to
  // browser-detected zone as the visible default (but we store empty
  // string to clear, not the browser zone itself).
  const [picked, setPicked] = React.useState<string>("");

  React.useEffect(() => {
    setPicked(savedTz ?? "");
  }, [savedTz]);

  // "dirty" means the local picker differs from the persisted value.
  const dirty = picked !== (savedTz ?? "");

  // Radix <SelectItem> forbids an empty-string value (#885). Use a
  // non-empty sentinel for the "no override" option and map it to/from
  // the empty string `picked` uses for "clear" so persistence is unchanged.
  const NO_TZ_OVERRIDE = "__browser__";

  async function save() {
    if (!dirty) return;
    const { data: res } = await updateProfile({
      variables: { input: { timezone: picked } },
    });
    if (res?.updateMyProfile.ok) {
      // Update the tz cookie immediately so the next server render
      // uses the new zone without waiting for a full page reload.
      if (typeof document !== "undefined") {
        const zone = picked || browserTz;
        document.cookie = `tz=${encodeURIComponent(zone)};path=/;max-age=31536000;SameSite=Lax`;
      }
      toast.success("Timezone saved");
    } else {
      toast.error(
        res?.updateMyProfile.errors[0]?.message ?? "Failed to save timezone",
      );
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Timezone</CardTitle>
        <CardDescription>
          Override the browser-detected timezone for all timestamps in the
          dashboard. Leave blank to use the zone your browser reports (
          <span className="font-mono text-2xs">{browserTz}</span>).
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 max-w-sm">
        <div className="space-y-2">
          <Label htmlFor="timezone">Timezone</Label>
          <Select
            value={picked || NO_TZ_OVERRIDE}
            onValueChange={(v) => setPicked(v === NO_TZ_OVERRIDE ? "" : v)}
          >
            <SelectTrigger id="timezone">
              <SelectValue
                placeholder={`${browserTz} (browser-detected)`}
              />
            </SelectTrigger>
            <SelectContent className="max-h-72">
              <SelectItem value={NO_TZ_OVERRIDE}>
                <span className="text-muted-foreground">
                  {browserTz} (browser-detected, no override)
                </span>
              </SelectItem>
              {TIMEZONES.map((tz) => (
                <SelectItem key={tz} value={tz}>
                  {tz}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Button
          size="sm"
          disabled={!dirty || saving}
          onClick={save}
        >
          {saving ? "Saving…" : "Save"}
        </Button>
      </CardContent>
    </Card>
  );
}
