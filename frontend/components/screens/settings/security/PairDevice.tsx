"use client";

/**
 * Mobile-device pairing card (#494).
 *
 * Operator clicks "Pair new device" → calls
 * ``generateInstallEnrollmentQr`` → renders the server-issued SVG QR
 * inline. Mobile scans → posts the enrollment token to
 * ``/api/cli/v1/auth/start`` → receives credentials without a
 * browser-approval step.
 *
 * Design notes
 *
 * * The QR SVG is rendered by the backend (pure-Python encoder) and
 *   surfaced as a literal markup string. We embed it via
 *   ``dangerouslySetInnerHTML`` because Strawberry returns a plain
 *   ``String!`` — the backend guarantees the SVG carries no
 *   JavaScript (see ``test_qr_svg_renders_without_javascript``).
 * * The countdown is driven off ``expiresAt``; when it reaches zero
 *   we mark the QR stale and prompt the operator to mint a new one.
 *   We don't auto-mint because that would silently burn an
 *   enrollment slot every 5 minutes the modal is left open.
 */

import {
  AlertTriangleIcon,
  CopyIcon,
  QrCodeIcon,
  RefreshCwIcon,
  SmartphoneIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
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

import type { usePairDevice } from "./use-pair-device";

export type PairDeviceViewProps = ReturnType<typeof usePairDevice>;

function useCountdown(expiresAt: string | undefined): number {
  // Returns seconds remaining until expiry, clamped at 0. Ticks once
  // per second. Returns 0 when the input is undefined so the UI can
  // render the "no QR yet" state on the same branch as expiry.
  const [now, setNow] = React.useState(() => Date.now());
  React.useEffect(() => {
    if (!expiresAt) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [expiresAt]);
  if (!expiresAt) return 0;
  const remaining = Math.max(0, Math.floor((Date.parse(expiresAt) - now) / 1000));
  return remaining;
}

function formatCountdown(seconds: number): string {
  const m = Math.floor(seconds / 60)
    .toString()
    .padStart(1, "0");
  const s = (seconds % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}

export function PairDeviceView({
  payload,
  loading,
  onMint,
  onCopyVerificationUri,
  onCopyPayload,
  onClear,
}: PairDeviceViewProps) {
  const t = useTranslations("settings.security.devices");
  const [open, setOpen] = React.useState(false);
  const [label, setLabel] = React.useState("");

  const remaining = useCountdown(payload?.expiresAt);
  const expired = payload !== null && remaining === 0;

  function reset() {
    onClear();
    setLabel("");
  }

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <SmartphoneIcon className="size-4" /> {t("title")}
          </CardTitle>
          <CardDescription>{t("description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <Button
            onClick={() => {
              reset();
              setOpen(true);
            }}
          >
            <QrCodeIcon className="size-4" />
            {t("buttonPair")}
          </Button>
        </CardContent>
      </Card>

      <Dialog
        open={open}
        onOpenChange={(next) => {
          setOpen(next);
          if (!next) reset();
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{t("dialogTitle")}</DialogTitle>
            <DialogDescription>{t("dialogDescription")}</DialogDescription>
          </DialogHeader>

          {!payload ? (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void onMint(label);
              }}
              className="space-y-4"
            >
              <div className="space-y-2">
                <Label htmlFor="device-label">{t("labelLabel")}</Label>
                <Input
                  id="device-label"
                  value={label}
                  onChange={(e) => setLabel(e.target.value)}
                  placeholder={t("labelPlaceholder")}
                  maxLength={128}
                  autoFocus
                />
                <p className="text-muted-foreground text-xs">{t("labelHelp")}</p>
              </div>
              <DialogFooter>
                <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
                  {t("buttonCancel")}
                </Button>
                <Button type="submit" disabled={loading}>
                  {loading ? t("buttonGenerating") : t("buttonGenerate")}
                </Button>
              </DialogFooter>
            </form>
          ) : (
            <div className="space-y-4">
              <div className="flex flex-col items-center gap-3 rounded-md border bg-white p-4">
                {/* Render server-issued SVG. Backend guarantees the
                    markup contains no script vectors — covered by
                    test_qr_svg_renders_without_javascript. */}
                <div
                  className="size-64"
                  aria-label={t("qrAriaLabel")}
                  // eslint-disable-next-line react/no-danger
                  dangerouslySetInnerHTML={{ __html: payload.qrSvg }}
                />
                <div className="flex items-center gap-2 text-sm">
                  {expired ? (
                    <span className="text-danger-fg flex items-center gap-1 font-medium">
                      <AlertTriangleIcon className="size-4" /> {t("expiredLabel")}
                    </span>
                  ) : (
                    <span className="text-muted-foreground tabular-nums">
                      {t("expiresIn", { time: formatCountdown(remaining) })}
                    </span>
                  )}
                </div>
              </div>

              <div className="space-y-2">
                <Label className="text-muted-foreground text-xs uppercase">
                  {t("sessionIdLabel")}
                </Label>
                <code className="bg-muted block truncate rounded px-2 py-1 font-mono text-xs">
                  {payload.sessionId}
                </code>
              </div>

              <div className="space-y-2">
                <Label className="text-muted-foreground text-xs uppercase">
                  {t("verificationUriLabel")}
                </Label>
                <div className="flex items-center gap-2">
                  <code className="bg-muted flex-1 truncate rounded px-2 py-1 font-mono text-xs">
                    {payload.verificationUri}
                  </code>
                  <Button type="button" variant="outline" size="sm" onClick={onCopyVerificationUri}>
                    <CopyIcon className="size-4" />
                  </Button>
                </div>
                <p className="text-muted-foreground text-xs">{t("verificationUriHelp")}</p>
              </div>

              <DialogFooter className="flex-row justify-between sm:justify-between">
                <Button type="button" variant="outline" onClick={onCopyPayload}>
                  <CopyIcon className="size-4" />
                  {t("buttonCopyPayload")}
                </Button>
                <div className="flex gap-2">
                  <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
                    {t("buttonDone")}
                  </Button>
                  <Button
                    type="button"
                    onClick={() => {
                      // Mints with the label as typed; reset() clears it
                      // for the next open, as before.
                      const current = label;
                      reset();
                      void onMint(current);
                    }}
                    disabled={loading}
                  >
                    <RefreshCwIcon className="size-4" />
                    {t("buttonRegenerate")}
                  </Button>
                </div>
              </DialogFooter>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
