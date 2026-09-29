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
import type { useStepUp } from "@/components/use-step-up";

export type StepUpPromptProps = ReturnType<typeof useStepUp>;

/** Pure (Storybook first): the flow comes from useStepUp, wired by the shell. */
export function StepUpPrompt({
  open,
  method,
  message,
  error,
  submitting: loading,
  onSubmitPassword,
  onSso: handleSsoElevate,
  onCancel,
}: StepUpPromptProps) {
  const t = useTranslations("stepUp");
  const [password, setPassword] = React.useState("");
  const passwordRef = React.useRef<HTMLInputElement>(null);

  // Focus the password field once the modal opens so the operator can
  // start typing immediately; matters on mobile, where it saves a tap.
  React.useEffect(() => {
    if (open) requestAnimationFrame(() => passwordRef.current?.focus());
  }, [open]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (await onSubmitPassword(password)) setPassword("");
  }

  function handleCancel() {
    setPassword("");
    onCancel();
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
            {message?.trim() || (ssoBranch ? t("sso.description") : t("description"))}
          </DialogDescription>
        </DialogHeader>
        {ssoBranch ? (
          <div className="space-y-4">
            <p className="text-muted-foreground text-sm">{t("sso.providerHint")}</p>
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
                <p id="step-up-error" className="text-destructive text-sm" role="alert">
                  {error}
                </p>
              ) : null}
            </div>
            <DialogFooter className="gap-2 sm:gap-0">
              <Button type="button" variant="outline" onClick={handleCancel} disabled={loading}>
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
