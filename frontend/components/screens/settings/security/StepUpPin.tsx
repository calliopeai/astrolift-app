"use client";

/**
 * Step-up PIN card (#2399): set, change or clear the PIN a person can
 * confirm sensitive actions with instead of signing in again. Only shown
 * when the org allows PINs, or when one is stored and can still be cleared.
 */

import { KeyRoundIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";

import type { useStepUpPin } from "./use-step-up-pin";

export type StepUpPinViewProps = ReturnType<typeof useStepUpPin>;

const PIN_PATTERN = /^\d{6,8}$/;

export function StepUpPinView({ status, loading, saving, onSet, onClear }: StepUpPinViewProps) {
  const t = useTranslations("settings.security.stepUpPin");
  const [pin, setPin] = React.useState("");
  const [confirm, setConfirm] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);

  if (!loading && (!status || (!status.orgAllows && !status.isSet))) return null;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!PIN_PATTERN.test(pin)) return setError(t("errorFormat"));
    if (pin !== confirm) return setError(t("errorMismatch"));
    setError(null);
    if (await onSet(pin)) {
      setPin("");
      setConfirm("");
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <KeyRoundIcon className="size-4" /> {t("title")}
          {status?.locked ? (
            <Badge variant="destructive">{t("badgeLocked")}</Badge>
          ) : status?.isSet ? (
            <Badge variant="outline">{t("badgeSet")}</Badge>
          ) : null}
        </CardTitle>
        <CardDescription>{t("description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {loading || !status ? (
          <Skeleton className="h-10 w-full max-w-md" />
        ) : !status.orgAllows ? (
          <div className="flex flex-wrap items-center gap-3">
            <p className="text-muted-foreground text-sm">{t("orgDisallows")}</p>
            <Button variant="outline" size="sm" onClick={onClear} disabled={saving}>
              {t("clear")}
            </Button>
          </div>
        ) : (
          <>
            {status.locked ? <p className="text-destructive text-sm">{t("locked")}</p> : null}
            <form onSubmit={handleSubmit} className="grid gap-3 sm:max-w-md sm:grid-cols-2">
              <div className="min-w-0 space-y-2">
                <Label htmlFor="step-up-pin">{status.isSet ? t("newPin") : t("pin")}</Label>
                <Input
                  id="step-up-pin"
                  type="password"
                  inputMode="numeric"
                  autoComplete="off"
                  maxLength={8}
                  className="font-mono tracking-widest"
                  value={pin}
                  onChange={(e) => setPin(e.target.value)}
                  disabled={saving}
                />
              </div>
              <div className="min-w-0 space-y-2">
                <Label htmlFor="step-up-pin-confirm">{t("confirm")}</Label>
                <Input
                  id="step-up-pin-confirm"
                  type="password"
                  inputMode="numeric"
                  autoComplete="off"
                  maxLength={8}
                  className="font-mono tracking-widest"
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                  disabled={saving}
                />
              </div>
              {error ? (
                <p className="text-destructive text-sm sm:col-span-2" role="alert">
                  {error}
                </p>
              ) : null}
              <p className="text-muted-foreground text-xs sm:col-span-2">{t("hint")}</p>
              <div className="flex flex-wrap gap-2 sm:col-span-2">
                <Button type="submit" disabled={saving}>
                  {status.isSet ? t("change") : t("set")}
                </Button>
                {status.isSet ? (
                  <Button type="button" variant="outline" onClick={onClear} disabled={saving}>
                    {t("clear")}
                  </Button>
                ) : null}
              </div>
            </form>
          </>
        )}
      </CardContent>
    </Card>
  );
}
