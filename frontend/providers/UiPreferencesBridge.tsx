"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useTheme } from "next-themes";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import type { AstroliftUiPreferences } from "@/graphql/__generated__/schema";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import type { MutationResult } from "@/graphql/identity/identity.types";
import { UPDATE_MY_UI_PREFERENCES } from "@/graphql/identity/ui-preferences.mutations";
import { MY_UI_PREFERENCES } from "@/graphql/identity/ui-preferences.queries";
import { appearanceFromServer, modeFor } from "@/lib/appearance";
import { applyServerDisplayPrefs } from "@/lib/display-prefs";
import { applyServerHomePrefs } from "@/lib/home-prefs";
import {
  type ServerUiPrefs,
  type UiPrefsPatch,
  setUiPrefsSaver,
  setUiPrefsStatus,
} from "@/lib/ui-prefs-sync";
import { applyServerVizPrefs } from "@/lib/viz-prefs";
import { useAppearance } from "@/providers/AppearanceProvider";

interface MyUiPreferencesData {
  astroliftMyUiPreferences: AstroliftUiPreferences | null;
}

interface UpdateMyUiPreferencesData {
  updateMyUiPreferences: MutationResult<AstroliftUiPreferences>;
}

/**
 * Makes the person's server preferences (#2154) the source of truth for the
 * browser-stored UI preferences (see lib/ui-prefs-sync.ts).
 *
 * Mounted inside the (app) layout, under `ActiveOrgProvider`: the read is
 * tenant scoped and only exists once signed in. The server's answer replaces
 * every store's browser copy; a change made on the page is written to the
 * browser copy at once and sent to the server, whose reply then settles it.
 * While a save is in flight the stores are left alone, so a slower reply to
 * an earlier change never flips the page back. A refused save (a value the
 * server does not accept) toasts and returns to the server's value; a save
 * that cannot reach the server leaves the browser copy until the next read.
 *
 * Renders nothing.
 */
export function UiPreferencesBridge() {
  const homeT = useTranslations("home.layoutSettings");
  const { org, loading: orgLoading } = useActiveOrg();
  const { data, error } = useQuery<MyUiPreferencesData>(MY_UI_PREFERENCES, {
    fetchPolicy: "cache-and-network",
    skip: !org,
  });
  const [update] = useMutation<UpdateMyUiPreferencesData, { input: UiPrefsPatch }>(
    UPDATE_MY_UI_PREFERENCES
  );
  const { applyServerAppearance } = useAppearance();
  const { setTheme } = useTheme();

  const server = data?.astroliftMyUiPreferences ?? null;
  const serverRef = React.useRef<ServerUiPrefs | null>(null);
  const pending = React.useRef(0);

  const apply = React.useCallback(
    (prefs: ServerUiPrefs) => {
      applyServerVizPrefs(prefs);
      applyServerDisplayPrefs(prefs);
      applyServerHomePrefs(prefs);
      const personal = appearanceFromServer(prefs.appearance);
      applyServerAppearance(personal);
      // A ground decides light or dark, as when the person picks one.
      if (personal?.ground) setTheme(modeFor(personal.ground));
    },
    [applyServerAppearance, setTheme]
  );

  React.useEffect(() => {
    setUiPrefsStatus("loading");
    return () => setUiPrefsStatus("local");
  }, []);

  const settled = data !== undefined || error !== undefined || (!org && !orgLoading);
  React.useEffect(() => {
    serverRef.current = server;
    if (server && pending.current === 0) apply(server);
    if (settled) setUiPrefsStatus(server ? "synced" : "local");
  }, [server, settled, apply]);

  React.useEffect(
    () =>
      setUiPrefsSaver((patch) => {
        pending.current += 1;
        let reached = true;
        update({
          variables: { input: patch },
          update(cache, { data: result }) {
            const res = result?.updateMyUiPreferences;
            if (res?.ok && res.data) {
              cache.writeQuery<MyUiPreferencesData>({
                query: MY_UI_PREFERENCES,
                data: { astroliftMyUiPreferences: res.data },
              });
            }
          },
        })
          .then(({ data: result }) => {
            const res = result?.updateMyUiPreferences;
            if (res?.ok && res.data) serverRef.current = res.data;
            else if (res)
              toast.error(
                res.errors[0]?.message ??
                  ("homeLayout" in patch ? homeT("saveFailed") : "Preference not saved")
              );
          })
          .catch(() => {
            // Offline or the server is down: the browser copy keeps the
            // change, and the next read settles it.
            reached = false;
          })
          .finally(() => {
            pending.current -= 1;
            if (reached && pending.current === 0 && serverRef.current) apply(serverRef.current);
          });
      }),
    [update, apply, homeT]
  );

  return null;
}
