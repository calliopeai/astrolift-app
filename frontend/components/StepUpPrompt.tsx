"use client";

/**
 * StepUpPrompt (#487) — modal that handles sensitive-operation
 * step-up auth.
 *
 * Listens for ``astrolift:step-up-required`` (dispatched by the
 * Apollo afterware when a mutation comes back with
 * ``STEP_UP_REQUIRED`` in its envelope). Opens a modal asking the
 * operator to confirm their password (the default verifier;
 * SSO-only installs swap in WebAuthn / passkey / OTP via the
 * backend's ``register_credential_verifier`` hook). On success
 * dispatches ``astrolift:elevated`` so callers that opted into
 * ``waitForElevation`` re-fire the original mutation.
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
} from "@/lib/auth/step-up-events";

type ElevateResponse = {
  elevateAdminSession: {
    ok: boolean;
    errors: MutationError[];
    data: AstroliftElevatePayload | null;
  };
};

export function StepUpPrompt() {
  const t = useTranslations("stepUp");
  const [open, setOpen] = React.useState(false);
  const [trigger, setTrigger] = React.useState<StepUpEventDetail | null>(null);
  const [password, setPassword] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const passwordRef = React.useRef<HTMLInputElement>(null);

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
      // tap.
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

  function handleCancel() {
    setOpen(false);
    setPassword("");
    // Notify waiters so they can drop the pending mutation instead
    // of timing out.
    window.dispatchEvent(new CustomEvent(DEELEVATED_EVENT));
  }

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
            {t("title")}
          </DialogTitle>
          <DialogDescription id="step-up-desc">
            {trigger?.message?.trim() || t("description")}
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="step-up-password" className="flex items-center gap-2">
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
      </DialogContent>
    </Dialog>
  );
}
