"use client";

/**
 * Nav-bar "Admin elevated for N more minutes" badge (#487).
 *
 * Polls ``astroliftElevationStatus`` so the timer stays accurate
 * across browser tabs that share the session cookie. The badge
 * also reacts to ``astrolift:elevated`` / ``astrolift:deelevated``
 * window events so the same-tab elevate / deelevate flow updates
 * without waiting for the next poll tick.
 *
 * One-click "deelevate" gives the operator the documented "log me
 * out of admin" escape hatch from the issue: useful on shared
 * machines after a sensitive write, or right after the operator
 * realises they walked away from an unlocked screen.
 */

import { ShieldCheckIcon, ShieldOffIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { useMutation, useQuery } from "@apollo/client/react";

import { Button } from "@/components/ui/button";
import { DEELEVATE_ADMIN_SESSION } from "@/graphql/identity/identity.mutations";
import { GET_ELEVATION_STATUS } from "@/graphql/identity/identity.queries";
import type {
  AstroliftDeelevatePayload,
  AstroliftElevationStatus,
  MutationError,
} from "@/graphql/identity/identity.types";
import {
  DEELEVATED_EVENT,
  ELEVATED_EVENT,
} from "@/lib/auth/step-up-events";

type StatusResp = { astroliftElevationStatus: AstroliftElevationStatus };
type DeelevateResp = {
  deelevateAdminSession: {
    ok: boolean;
    errors: MutationError[];
    data: AstroliftDeelevatePayload | null;
  };
};

// Refetch the elevation status every 30s. A real-time countdown
// would refetch every second; 30s is enough resolution for the
// "5 more minutes" copy without flooding the backend with no-op
// queries from every open tab. Local same-tab events bridge the
// gap when the operator elevates/deelevates from this tab.
const POLL_MS = 30_000;

export function ElevationIndicator() {
  const t = useTranslations("stepUp");
  const { data, refetch } = useQuery<StatusResp>(GET_ELEVATION_STATUS, {
    pollInterval: POLL_MS,
    // Cache-and-network so the first paint shows the cached state
    // while the live query lands.
    fetchPolicy: "cache-and-network",
    notifyOnNetworkStatusChange: false,
  });
  const [deelevate, { loading: deelevating }] = useMutation<DeelevateResp>(
    DEELEVATE_ADMIN_SESSION,
    { refetchQueries: ["GetElevationStatus"] },
  );

  React.useEffect(() => {
    function refresh() {
      // Best-effort refetch — Apollo will swallow + log on network
      // error rather than throw.
      void refetch();
    }
    window.addEventListener(ELEVATED_EVENT, refresh);
    window.addEventListener(DEELEVATED_EVENT, refresh);
    return () => {
      window.removeEventListener(ELEVATED_EVENT, refresh);
      window.removeEventListener(DEELEVATED_EVENT, refresh);
    };
  }, [refetch]);

  const status = data?.astroliftElevationStatus;
  if (!status?.elevated) return null;

  const minutes = Math.max(1, Math.round(status.secondsRemaining / 60));

  async function handleDeelevate() {
    try {
      await deelevate();
      window.dispatchEvent(new CustomEvent(DEELEVATED_EVENT));
    } catch (err) {
      console.error("[ElevationIndicator] deelevate failed:", err);
    }
  }

  return (
    <div
      className="bg-primary/10 text-primary inline-flex items-center gap-2 rounded-md border border-current/20 px-2 py-1 text-xs font-medium"
      role="status"
      aria-live="polite"
      data-testid="elevation-indicator"
    >
      <ShieldCheckIcon className="size-3.5" aria-hidden />
      <span>{t("indicator", { minutes })}</span>
      <Button
        type="button"
        size="sm"
        variant="ghost"
        className="h-6 px-1 py-0 text-xs hover:bg-current/10"
        onClick={handleDeelevate}
        disabled={deelevating}
        aria-label={t("deelevateLabel")}
        title={t("deelevateLabel")}
      >
        <ShieldOffIcon className="size-3.5" aria-hidden />
      </Button>
    </div>
  );
}
