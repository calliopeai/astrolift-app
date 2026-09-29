"use client";

import type * as React from "react";

import { useDisplayPrefs, type RestrictedSettings } from "@/lib/display-prefs";

/**
 * A part of a settings page the viewer may not change. Following the
 * person's display preference, it shows the same fields disabled with the
 * permission that would allow it (spec 44 §5.3), or leaves them out.
 */
export interface RestrictedProps {
  allowed: boolean;
  permission: string;
  /** What the permission unlocks, as the note's subject. */
  verb?: string;
  /** Overrides the person's preference (stories, previews). */
  mode?: RestrictedSettings;
  children: React.ReactNode;
}

export function Restricted({
  allowed,
  permission,
  verb = "Changing these",
  mode,
  children,
}: RestrictedProps) {
  const effective = useRestrictedMode(mode);
  if (allowed) return <>{children}</>;
  if (effective === "hide") return null;
  return (
    <div className="flex min-w-0 flex-col gap-3">
      <PermissionNote permission={permission} verb={verb} />
      <fieldset disabled className="min-w-0">
        {children}
      </fieldset>
    </div>
  );
}

/** The person's choice unless a caller pins one. */
export function useRestrictedMode(mode?: RestrictedSettings): RestrictedSettings {
  const [prefs] = useDisplayPrefs();
  return mode ?? prefs.restrictedSettings;
}

export function PermissionNote({ permission, verb }: { permission: string; verb: string }) {
  return (
    <p className="bg-muted text-muted-foreground rounded-md border px-3 py-2 text-sm">
      {verb} needs the{" "}
      <code className="text-foreground font-mono [overflow-wrap:anywhere]">{permission}</code>{" "}
      permission.
    </p>
  );
}
