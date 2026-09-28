"use client";

import * as React from "react";

import { useAppearance } from "@/providers/AppearanceProvider";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { normalizePartial, type Appearance } from "@/lib/appearance";

/**
 * Feeds the org's house theme into the appearance provider (#135).
 *
 * The provider lives in the root layout because the pre-paint script and the
 * personal preference apply everywhere, including the login pages. The org
 * policy can only be read once authenticated, so it arrives here instead —
 * mounted inside the (app) layout, under `ActiveOrgProvider`.
 *
 * Renders nothing. The org is already fetched for the whole (app) tree, so
 * this adds no request.
 */
export function AppearancePolicyBridge() {
  const { org } = useActiveOrg();
  const { setPolicy } = useAppearance();

  const raw = org?.appearanceDefault;
  const locked = Boolean(org?.appearanceLocked);

  // Serialize for the dependency list: `appearanceDefault` is a JSON scalar,
  // so Apollo hands back a fresh object identity on every cache read and an
  // object dep would re-run this effect forever.
  const key = React.useMemo(() => JSON.stringify(raw ?? null), [raw]);

  React.useEffect(() => {
    if (!org) return;
    const parsed = key ? (JSON.parse(key) as unknown) : null;
    // Trust nothing, and keep a partial partial: an org that pins only the
    // accent must not thereby pin ground, density and corners too.
    // `normalizePartial` drops unknown keys and values without filling the
    // rest, so an axis renamed since the policy was written degrades to
    // "not set" instead of to the shipped default.
    const filtered = normalizePartial(parsed);
    const orgDefault: Partial<Appearance> | null =
      Object.keys(filtered).length > 0 ? filtered : null;
    setPolicy({ orgDefault, locked });
  }, [org, key, locked, setPolicy]);

  return null;
}
