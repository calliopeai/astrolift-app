"use client";

/**
 * StepUpPrompt (#487, #526) — modal that handles sensitive-operation
 * step-up auth.
 *
 * Listens for ``astrolift:step-up-required`` (dispatched by the
 * Apollo afterware when a mutation comes back with
 * ``STEP_UP_REQUIRED`` in its envelope). Branches on
 * ``supportedMethods`` from the deny envelope:
 *
 *   password  → existing form (the #487 default)
 *   sso       → "Re-authenticate" button → /app/auth1/elevate-sso?return=...
 *   webauthn  → stubbed out-of-scope for #526, falls back to password
 *
 * SSO-only installs (operators with an unusable Django password hash)
 * couldn't satisfy the password prompt — that was the prod blocker
 * for #526. The SSO branch redirects the operator through the IdP
 * with ``prompt=login&max_age=0`` so the IdP forces a fresh
 * authentication; on callback the session elevates and the operator
 * lands back on the page they started from.
 *
 * Mounted in the app-shell layout next to ``SessionExpiredModal``
 * so any mutation, anywhere in the tree, can trigger it.
 */

import { LockKeyholeIcon, ShieldCheckIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { useMutation } from "@apollo/client/react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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

export function StepUpPrompt() {
  const t = useTranslations("stepUp");
  const [open, setOpen] = React.useState(false);
  const [trigger, setTrigger] = React.useState<StepUpEventDetail | null>(null);
  const [password, setPassword] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const passwordRef = React.useRef<HTMLInputElement>(null);

  const method: StepUpMethod = React.useMemo(
    () => pickMethod(trigger?.supportedMethods),
    [trigger],
  );

  const [elevate, { loading }] = useMutation<
    ElevateResponse,
    { input: ElevateAdminSessionInput }
  >(ELEVATE_ADMIN_SESSION, {
    // The elevation timer is server-side state — clear any cached
    // ``astroliftElevationStatus`` so the nav indicator picks up
    // the fresh expiry on the next render.
    refetchQueries: ["GetElevationStatus"],
  });

  React.useEffect(() => {
    function onStepUp(e: Event) {
      const detail = (e as CustomEvent<StepUpEventDetail>).detail;
      setTrigger(detail);
      setOpen(true);
      setError(null);
      setPassword("");
      // Focus the password field once the modal renders so the
      // operator can start typing immediately — small touch but
      // matters for mobile, where surfacing the keyboard saves a
      // tap. Only relevant on the password branch.
      requestAnimationFrame(() => passwordRef.current?.focus());
    }
    window.addEventListener(STEP_UP_EVENT, onStepUp);
    return () => window.removeEventListener(STEP_UP_EVENT, onStepUp);
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!password) {
      setError(t("errors.passwordRequired"));
      return;
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
        return;
      }
      const detail: ElevatedEventDetail = {
        elevatedUntil: payload.data.elevatedUntil,
        secondsRemaining: payload.data.secondsRemaining,
        method: payload.data.method,
      };
      window.dispatchEvent(new CustomEvent(ELEVATED_EVENT, { detail }));
      setOpen(false);
      setPassword("");
    } catch (err) {
      // Network error — surface generic copy, don't blow up the modal.
      setError(t("errors.network"));
      console.error("[StepUpPrompt] elevate failed:", err);
    }
  }

  /**
   * SSO branch: redirect the browser through the IdP. The afterware
   * that triggered this modal already cancelled the original
   * mutation, so the operator-visible state is just "modal open" —
   * navigating away is safe. The backend callback elevates the
   * session and 302s back to the path encoded in ``return``.
   */
  function handleSsoElevate() {
    if (typeof window === "undefined") return;
    window.location.assign(ssoElevateUrl());
  }

  function handleCancel() {
    setOpen(false);
    setPassword("");
    // Notify waiters so they can drop the pending mutation instead
    // of timing out.
    window.dispatchEvent(new CustomEvent(DEELEVATED_EVENT));
  }

  const ssoBranch = method === "sso";

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) handleCancel();
      }}
    >
      <DialogContent
        className="sm:max-w-md"
        aria-labelledby="step-up-title"
        aria-describedby="step-up-desc"
      >
        <DialogHeader>
          <DialogTitle id="step-up-title" className="flex items-center gap-2">
            <ShieldCheckIcon className="size-5" aria-hidden />
            {ssoBranch ? t("sso.title") : t("title")}
          </DialogTitle>
          <DialogDescription id="step-up-desc">
            {trigger?.message?.trim() ||
              (ssoBranch ? t("sso.description") : t("description"))}
          </DialogDescription>
        </DialogHeader>
        {ssoBranch ? (
          <div className="space-y-4">
            <p className="text-muted-foreground text-sm">
              {t("sso.providerHint")}
            </p>
            <DialogFooter className="gap-2 sm:gap-0">
              <Button type="button" variant="outline" onClick={handleCancel}>
                {t("sso.cancel")}
              </Button>
              <Button type="button" onClick={handleSsoElevate}>
                {t("sso.action")}
              </Button>
            </DialogFooter>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label
                htmlFor="step-up-password"
                className="flex items-center gap-2"
              >
                <LockKeyholeIcon className="size-4" aria-hidden />
                {t("passwordLabel")}
              </Label>
              <Input
                ref={passwordRef}
                id="step-up-password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={loading}
                aria-invalid={error ? true : undefined}
                aria-describedby={error ? "step-up-error" : undefined}
              />
              {error ? (
                <p
                  id="step-up-error"
                  className="text-destructive text-sm"
                  role="alert"
                >
                  {error}
                </p>
              ) : null}
            </div>
            <DialogFooter className="gap-2 sm:gap-0">
              <Button
                type="button"
                variant="outline"
                onClick={handleCancel}
                disabled={loading}
              >
                {t("cancel")}
              </Button>
              <Button type="submit" disabled={loading}>
                {loading ? t("elevating") : t("confirm")}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
