"use client";

import { useTranslations } from "next-intl";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";

import type { AppDomain } from "./use-app-domains";

// ─── UploadCertSheet (#397 BYO path) ────────────────────────────────────
// Paste-in flow for operator-supplied PEM chain + private key. The
// backend stores the bundle; the renderer projects it as a K8s Secret
// on the runtime cluster on the next deploy.

export interface UploadCertSheetProps {
  domain: AppDomain | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (certificatePem: string, privateKeyPem: string) => Promise<boolean>;
  busy: boolean;
}

export function UploadCertSheet({
  domain,
  open,
  onOpenChange,
  onSubmit,
  busy,
}: UploadCertSheetProps) {
  const t = useTranslations("apps.domains.upload");
  const tCommon = useTranslations("apps.common");
  const [cert, setCert] = React.useState("");
  const [key, setKey] = React.useState("");

  React.useEffect(() => {
    if (!open) {
      setCert("");
      setKey("");
    }
  }, [open]);

  const certOk = cert.includes("-----BEGIN CERTIFICATE-----");
  const keyOk = key.includes("PRIVATE KEY");
  const ready = certOk && keyOk && !busy;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col sm:max-w-2xl">
        <SheetHeader>
          <SheetTitle>
            {domain ? t("title", { hostname: domain.hostname }) : t("fallbackTitle")}
          </SheetTitle>
          <SheetDescription>{t("description")}</SheetDescription>
        </SheetHeader>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            if (!ready) return;
            await onSubmit(cert.trim(), key.trim());
          }}
          className="flex flex-1 flex-col gap-4 px-4 pb-4"
        >
          <div className="space-y-2">
            <Label htmlFor="cert-pem">{t("certLabel")}</Label>
            <Textarea
              id="cert-pem"
              value={cert}
              onChange={(e) => setCert(e.target.value)}
              placeholder={
                "-----BEGIN CERTIFICATE-----\n...leaf...\n-----END CERTIFICATE-----\n-----BEGIN CERTIFICATE-----\n...intermediate...\n-----END CERTIFICATE-----"
              }
              spellCheck={false}
              required
              className="h-40 font-mono text-xs"
            />
            {!certOk && cert.length > 0 && (
              <p className="text-destructive text-xs">{t("certInvalid")}</p>
            )}
          </div>
          <div className="space-y-2">
            <Label htmlFor="cert-key">{t("keyLabel")}</Label>
            <Textarea
              id="cert-key"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder={"-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----"}
              spellCheck={false}
              required
              className="h-32 font-mono text-xs"
            />
            {!keyOk && key.length > 0 && (
              <p className="text-destructive text-xs">{t("keyInvalid")}</p>
            )}
          </div>
          <p className="text-muted-foreground text-xs">{t("noRotate")}</p>
          <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              {tCommon("cancel")}
            </Button>
            <Button type="submit" disabled={!ready}>
              {busy ? t("submitting") : t("submit")}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
