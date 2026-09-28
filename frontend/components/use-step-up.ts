"use client";

import { useMutation } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { ELEVATE_ADMIN_SESSION } from "@/graphql/identity/identity.mutations";
import type {
  AstroliftElevatePayload,
  ElevateAdminSessionInput,
} from "@/graphql/identity/identity.types";
import type { MutationError } from "@/graphql/identity/identity.types";
import {
  DEELEVATED_EVENT,
  ELEVATED_EVENT,
  STEP_UP_EVENT,
  type ElevatedEventDetail,
  type StepUpEventDetail,
  type StepUpMethod,
} from "@/lib/auth/step-up-events";

/**
 * Admin step-up (#487): opened by the step-up event any mutation can raise,
 * elevating by password or SSO. The data half of StepUpPrompt.
 */
type ElevateResponse = {
  elevateAdminSession: {
    ok: boolean;
    errors: MutationError[];
    data: AstroliftElevatePayload | null;
  };
};

/**
 * Pick the credential family the modal should drive. The backend
 * returns one value per current session; we honour the first known
 * method in the list. Missing / empty input falls back to password
 * (the pre-#526 behaviour) so legacy callers don't regress.
 */
function pickMethod(supported: StepUpMethod[] | undefined): StepUpMethod {
  if (!supported || supported.length === 0) return "password";
  // First-known-wins. SSO before password, password before webauthn:
  // matches the backend's per-session "one method at a time" contract
  // and keeps the modal deterministic when a buggy backend returns
  // an unexpected mix.
  const order: StepUpMethod[] = ["sso", "password", "webauthn", "magic_link"];
  for (const candidate of order) {
    if (supported.includes(candidate)) return candidate;
  }
  return "password";
}

/**
 * Build the absolute SSO-elevate URL with a safe relative ``return``
 * pointing back to the current page so the IdP round-trip drops the
 * operator where they started. The backend's ``elevate_sso_start``
 * view re-validates the return param server-side.
 */
function ssoElevateUrl(): string {
  if (typeof window === "undefined") return "/app/auth1/elevate-sso/";
  const relative = window.location.pathname + window.location.search;
  return `/app/auth1/elevate-sso/?return=${encodeURIComponent(relative)}`;
}

export function useStepUp() {
  const t = useTranslations("stepUp");
  const [open, setOpen] = React.useState(false);
  const [trigger, setTrigger] = React.useState<StepUpEventDetail | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  const method: StepUpMethod = React.useMemo(
    () => pickMethod(trigger?.supportedMethods),
    [trigger]
  );

  const [elevate, { loading }] = useMutation<ElevateResponse, { input: ElevateAdminSessionInput }>(
    ELEVATE_ADMIN_SESSION,
    {
      // The elevation timer is server-side state — clear any cached
      // ``astroliftElevationStatus`` so the nav indicator picks up
      // the fresh expiry on the next render.
      refetchQueries: ["GetElevationStatus"],
    }
  );

  React.useEffect(() => {
    function onStepUp(e: Event) {
      const detail = (e as CustomEvent<StepUpEventDetail>).detail;
      setTrigger(detail);
      setOpen(true);
      setError(null);
    }
    window.addEventListener(STEP_UP_EVENT, onStepUp);
    return () => window.removeEventListener(STEP_UP_EVENT, onStepUp);
  }, []);

  /** True when elevated; the dialog then closes. */
  async function onSubmitPassword(password: string): Promise<boolean> {
    setError(null);
    if (!password) {
      setError(t("errors.passwordRequired"));
      return false;
    }
    try {
      const { data } = await elevate({
        variables: {
          input: { method: "password", credential: password },
        },
      });
      const payload = data?.elevateAdminSession;
      if (!payload?.ok || !payload.data) {
        const firstError = payload?.errors?.[0]?.message ?? t("errors.generic");
        setError(firstError);
        return false;
      }
      const detail: ElevatedEventDetail = {
        elevatedUntil: payload.data.elevatedUntil,
        secondsRemaining: payload.data.secondsRemaining,
        method: payload.data.method,
      };
      window.dispatchEvent(new CustomEvent(ELEVATED_EVENT, { detail }));
      setOpen(false);
      return true;
    } catch (err) {
      // Network error — surface generic copy, don't blow up the modal.
      setError(t("errors.network"));
      console.error("[StepUpPrompt] elevate failed:", err);
      return false;
    }
  }

  /**
   * SSO branch: redirect the browser through the IdP. The afterware
   * that triggered this modal already cancelled the original
   * mutation, so the operator-visible state is just "modal open" —
   * navigating away is safe. The backend callback elevates the
   * session and 302s back to the path encoded in ``return``.
   */
  function onSso() {
    if (typeof window === "undefined") return;
    window.location.assign(ssoElevateUrl());
  }

  function onCancel() {
    setOpen(false);
    // Notify waiters so they can drop the pending mutation instead
    // of timing out.
    window.dispatchEvent(new CustomEvent(DEELEVATED_EVENT));
  }

  return {
    open,
    method,
    message: trigger?.message ?? null,
    error,
    submitting: loading,
    onSubmitPassword,
    onSso,
    onCancel,
  };
}
