"use client";

import { useApolloClient, useQuery } from "@apollo/client/react";
import * as React from "react";

import type { GetAccessRevisionQuery } from "@/graphql/__generated__/operations";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { GET_ACCESS_REVISION } from "@/graphql/permissions/astrolift.queries";

/** How often the revision is asked for while the tab stays open. */
export const ACCESS_REVISION_POLL_MS = 60_000;

/**
 * Drops cached capabilities when the server says they may have changed
 * (#1867). `astroliftAccessRevision` moves whenever the viewer's roles,
 * attributes, policies or session do; it is asked for every minute and when
 * the tab regains focus. When it differs from the revision the cache was
 * filled under, the Apollo store is reset: cached data is discarded (it may
 * hold objects the viewer can no longer see) and the active queries refetch,
 * so `astroliftMyPermissions` and every `viewerPermissions` come back fresh.
 *
 * Mounted inside the (app) layout, under `ActiveOrgProvider`: the read is
 * tenant scoped. Renders nothing. The server still decides every action.
 */
export function AccessRevisionBridge() {
  const client = useApolloClient();
  const { org } = useActiveOrg();
  const { data, refetch } = useQuery<GetAccessRevisionQuery>(GET_ACCESS_REVISION, {
    fetchPolicy: "network-only",
    pollInterval: ACCESS_REVISION_POLL_MS,
    skip: !org,
  });
  const revision = data?.astroliftAccessRevision || null;
  const seen = React.useRef<string | null>(null);

  React.useEffect(() => {
    if (!revision) return;
    if (seen.current !== null && seen.current !== revision) {
      // A reset racing an in-flight query rejects; the refetch it starts still lands.
      void client.resetStore().catch(() => {});
    }
    seen.current = revision;
  }, [client, revision]);

  React.useEffect(() => {
    if (!org) return;
    const onFocus = () => void refetch().catch(() => {});
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [org, refetch]);

  return null;
}
