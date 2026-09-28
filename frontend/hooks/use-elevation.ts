"use client";

/**
 * Admin elevation for the signed-in session (#487): polled so the timer
 * stays right across tabs sharing the cookie, refreshed on the same-tab
 * elevate and deelevate events, with the one-click deelevate. The data half
 * of ElevationIndicator.
 */

import { useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";

import { DEELEVATE_ADMIN_SESSION } from "@/graphql/identity/identity.mutations";
import { GET_ELEVATION_STATUS } from "@/graphql/identity/identity.queries";
import type {
  AstroliftDeelevatePayload,
  AstroliftElevationStatus,
  MutationError,
} from "@/graphql/identity/identity.types";
import { DEELEVATED_EVENT, ELEVATED_EVENT } from "@/lib/auth/step-up-events";

type StatusResp = { astroliftElevationStatus: AstroliftElevationStatus };
type DeelevateResp = {
  deelevateAdminSession: {
    ok: boolean;
    errors: MutationError[];
    data: AstroliftDeelevatePayload | null;
  };
};

const POLL_MS = 30_000;

export function useElevation() {
  const { data, refetch } = useQuery<StatusResp>(GET_ELEVATION_STATUS, {
    pollInterval: POLL_MS,
    fetchPolicy: "cache-and-network",
    notifyOnNetworkStatusChange: false,
  });
  const [deelevate, { loading: deelevating }] = useMutation<DeelevateResp>(
    DEELEVATE_ADMIN_SESSION,
    { refetchQueries: ["GetElevationStatus"] }
  );

  React.useEffect(() => {
    function refresh() {
      void refetch();
    }
    window.addEventListener(ELEVATED_EVENT, refresh);
    window.addEventListener(DEELEVATED_EVENT, refresh);
    return () => {
      window.removeEventListener(ELEVATED_EVENT, refresh);
      window.removeEventListener(DEELEVATED_EVENT, refresh);
    };
  }, [refetch]);

  async function onDeelevate() {
    try {
      await deelevate();
      window.dispatchEvent(new CustomEvent(DEELEVATED_EVENT));
    } catch (err) {
      console.error("[ElevationIndicator] deelevate failed:", err);
    }
  }

  const status = data?.astroliftElevationStatus;
  return {
    elevated: Boolean(status?.elevated),
    secondsRemaining: status?.secondsRemaining ?? 0,
    deelevating,
    onDeelevate,
  };
}
