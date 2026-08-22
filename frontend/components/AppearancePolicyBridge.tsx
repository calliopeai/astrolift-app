"use client";

import * as React from "react";

import { useAppearance } from "@/components/AppearanceProvider";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { normalize, type Appearance } from "@/lib/appearance";

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
    // Trust nothing: an org default written before an axis was renamed would
    // otherwise pin every user to a value the client no longer understands.
    // `normalize` maps anything unknown back onto the shipped default.
    const orgDefault: Partial<Appearance> | null =
      parsed && typeof parsed === "object" && Object.keys(parsed).length > 0
        ? normalize(parsed)
        : null;
    setPolicy({ orgDefault, locked });
  }, [org, key, locked, setPolicy]);

  return null;
}
