"use client";

import { KeyRoundIcon } from "lucide-react";
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

import type { RotateFactoryGateKeyData } from "./use-factory-gate-app";

export type RotateFactoryGateKeySheetProps = RotateFactoryGateKeyData & {
  open: boolean;
  onOpenChange: (next: boolean) => void;
  /** The App's GitHub organization, for the link to its App settings. */
  owner: string | null;
};

/**
 * Replace the factory gate App's private key (#2589). GitHub has no API that
 * issues App keys, so the operator generates one in the App's settings and
 * pastes it here; the server checks it with GitHub before storing it. The
 * pasted key is dropped from the form when the sheet closes.
 */
export function RotateFactoryGateKeySheet({
  open,
  onOpenChange,
  owner,
  rotating,
  rotate,
}: RotateFactoryGateKeySheetProps) {
  const t = useTranslations("sourceProviders.factoryGate.rotateSheet");
  const [pem, setPem] = React.useState("");
  const canSubmit = pem.trim() !== "" && !rotating;

  function close(next: boolean) {
    if (!next) setPem("");
    onOpenChange(next);
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (canSubmit && (await rotate(pem))) close(false);
  }

  return (
    <Sheet open={open} onOpenChange={close}>
      <SheetContent className="flex flex-col">
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            <KeyRoundIcon className="size-4" />
            {t("title")}
          </SheetTitle>
          <SheetDescription>{t("description")}</SheetDescription>
        </SheetHeader>

        <form onSubmit={submit} className="flex flex-1 flex-col gap-4 overflow-y-auto px-4 pb-4">
          {owner && (
            <a
              href={`https://github.com/organizations/${encodeURIComponent(owner)}/settings/apps`}
              target="_blank"
              rel="noreferrer noopener"
              className="text-xs break-all underline"
            >
              {t("openGithub", { owner })}
            </a>
          )}
          <div className="space-y-2">
            <Label htmlFor="factory-gate-pem">{t("label")}</Label>
            <Textarea
              id="factory-gate-pem"
              value={pem}
              onChange={(e) => setPem(e.target.value)}
              placeholder={"-----BEGIN RSA PRIVATE KEY-----\n…\n-----END RSA PRIVATE KEY-----"}
              className="h-40 font-mono text-xs"
              autoComplete="off"
              spellCheck={false}
              required
            />
            <p className="text-muted-foreground text-xs">{t("help")}</p>
          </div>

          <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
            <Button type="button" variant="outline" onClick={() => close(false)}>
              {t("cancel")}
            </Button>
            <Button type="submit" disabled={!canSubmit}>
              {rotating ? t("submitting") : t("submit")}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
