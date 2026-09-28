"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import { useCursorTable, type CursorPage } from "@/components/data-table";
import type { MutationResult } from "@/graphql/identity/identity.types";
import {
  CLEAR_ALERT_SUBSCRIPTION,
  SET_ALERT_SUBSCRIPTION,
} from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_MY_ALERT_SUBSCRIPTIONS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftUserAlertSubscription } from "@/graphql/lifecycle/lifecycle.types";
import { LIST_APPS_PAGE } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

import { ALERT_KINDS, subKey } from "./alert-kinds";

// The in-app/web notifications surface maps to the backend's "web" channel
// (UserAlertSubscription.Channel = email | web | both). "in_app" is not a
// valid value and the subscribe mutation rejects it (#886).
const CHANNEL = "web";

interface AppsPageResp {
  astroliftAppsPage: CursorPage<AstroliftRegisteredApp>;
}

interface SubsResp {
  astroliftMyAlertSubscriptions: AstroliftUserAlertSubscription[];
}

/**
 * The app x alert-kind subscription matrix: a server-paged app list, the
 * caller's web-channel subscriptions, and the set/clear mutations. The data
 * half of AlertSubscriptionsView.
 */
export function useAlertSubscriptions() {
  // `astroliftAppsPage` takes `search`, `limit` and `cursor` (not `after`),
  // and no sort argument — so the filter is a server argument and no column
  // declares a `sortKey`. The previous version asked for 200 apps and
  // filtered them in the browser, which silently hid app 201.
  const table = useCursorTable<AstroliftRegisteredApp>({
    query: LIST_APPS_PAGE,
    extract: (d) => (d as AppsPageResp | undefined)?.astroliftAppsPage,
    searchVariable: "search",
    cursorVariable: "cursor",
    urlKey: "alerts",
  });

  const subs = useQuery<SubsResp>(LIST_MY_ALERT_SUBSCRIPTIONS, {
    variables: { appSlug: null },
    fetchPolicy: "cache-and-network",
  });

  const [setSubscription, setState] = useMutation<{
    setAlertSubscription: MutationResult<AstroliftUserAlertSubscription>;
  }>(SET_ALERT_SUBSCRIPTION, {
    refetchQueries: [{ query: LIST_MY_ALERT_SUBSCRIPTIONS, variables: { appSlug: null } }],
    awaitRefetchQueries: true,
  });
  const [clearSubscription, clearState] = useMutation<{
    clearAlertSubscription: MutationResult<AstroliftUserAlertSubscription>;
  }>(CLEAR_ALERT_SUBSCRIPTION, {
    refetchQueries: [{ query: LIST_MY_ALERT_SUBSCRIPTIONS, variables: { appSlug: null } }],
    awaitRefetchQueries: true,
  });

  // Also blocked while the subscription list is in flight: a checkbox whose
  // current state hasn't loaded yet would render off and toggle to a value
  // the operator didn't choose.
  const busy = setState.loading || clearState.loading || subs.loading;

  const subMap = React.useMemo(() => {
    const map = new Map<string, AstroliftUserAlertSubscription>();
    for (const s of subs.data?.astroliftMyAlertSubscriptions ?? []) {
      if (s.channel !== CHANNEL) continue;
      map.set(subKey(s.appSlug, s.alertKind), s);
    }
    return map;
  }, [subs.data?.astroliftMyAlertSubscriptions]);

  async function setEnabled(appSlug: string, alertKind: string, enabled: boolean) {
    const { data } = await setSubscription({
      variables: {
        input: { appSlug, alertKind, channel: CHANNEL, enabled },
      },
    });
    if (!data?.setAlertSubscription.ok) {
      toast.error(data?.setAlertSubscription.errors?.[0]?.message ?? "Save failed");
    }
  }

  /** One matrix cell: unchecking an existing subscription clears it. */
  async function onToggle(appSlug: string, alertKind: string, enabled: boolean) {
    const sub = subMap.get(subKey(appSlug, alertKind)) ?? null;
    if (!enabled && sub) {
      await clearSubscription({ variables: { input: { id: sub.id } } });
    } else {
      await setEnabled(appSlug, alertKind, enabled);
    }
  }

  async function onSubscribeAll(appSlug: string) {
    for (const kind of ALERT_KINDS) {
      const existing = subMap.get(subKey(appSlug, kind.value));
      if (!existing || !existing.enabled) {
        await setEnabled(appSlug, kind.value, true);
      }
    }
    toast.success(`Subscribed to all on ${appSlug}`);
  }

  // Throws on the first failure so ConfirmDialog keeps the dialog open and
  // reports which clear failed, rather than closing on a partial unsubscribe.
  async function onUnsubscribeAll(appSlug: string) {
    for (const kind of ALERT_KINDS) {
      const existing = subMap.get(subKey(appSlug, kind.value));
      if (!existing) continue;
      const { data } = await clearSubscription({
        variables: { input: { id: existing.id } },
      });
      if (!data?.clearAlertSubscription.ok) {
        throw new Error(data?.clearAlertSubscription.errors?.[0]?.message ?? "Unsubscribe failed");
      }
    }
    toast.success(`Unsubscribed from ${appSlug}`);
  }

  return { table, subMap, busy, onToggle, onSubscribeAll, onUnsubscribeAll };
}
