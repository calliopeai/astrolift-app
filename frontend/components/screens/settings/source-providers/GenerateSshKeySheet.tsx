"use client";

import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import type { AstroliftSshDeployKeyCreated } from "@/graphql/scm/scm.types";

import type { GenerateSshKeyData } from "./use-source-providers";

export type GenerateSshKeySheetProps = GenerateSshKeyData & {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Stories only: open straight onto the generated-key step. */
  initialKey?: AstroliftSshDeployKeyCreated["key"] | null;
};

export function GenerateSshKeySheet({
  open,
  onOpenChange,
  generating,
  generate,
  initialKey = null,
}: GenerateSshKeySheetProps) {
  const t = useTranslations("sourceProviders.generateKey");
  const [name, setName] = React.useState("");
  const [appSlug, setAppSlug] = React.useState("");
  const [pasted, setPasted] = React.useState<AstroliftSshDeployKeyCreated["key"] | null>(
    initialKey
  );

  React.useEffect(() => {
    if (!open) {
      setName("");
      setAppSlug("");
      setPasted(null);
    }
  }, [open]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    const key = await generate(name.trim(), appSlug.trim() || null);
    if (key) setPasted(key);
  }

  async function copyPublicKey(value: string) {
    try {
      await navigator.clipboard.writeText(value);
      toast.success(t("copied"));
    } catch {
      toast.error(t("copyFailed"));
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col">
        <SheetHeader>
          <SheetTitle>{t("title")}</SheetTitle>
          <SheetDescription>{t("description")}</SheetDescription>
        </SheetHeader>
        {pasted ? (
          <div className="flex flex-1 flex-col gap-4 overflow-y-auto px-4 pb-4">
            <div className="space-y-2">
              <Label>{t("fingerprint")}</Label>
              <div className="font-mono text-xs [overflow-wrap:anywhere]">
                {pasted.fingerprintSha256}
              </div>
            </div>
            <div className="space-y-2">
              <Label>{t("publicKey")}</Label>
              <textarea
                readOnly
                rows={4}
                value={pasted.publicKey}
                className="bg-muted text-2xs rounded-md border p-2 font-mono"
                onClick={(e) => (e.target as HTMLTextAreaElement).select()}
              />
              <Button size="sm" variant="outline" onClick={() => copyPublicKey(pasted.publicKey)}>
                {t("copyPublicKey")}
              </Button>
            </div>
            <p className="text-muted-foreground text-xs">
              {t.rich("help", { code: (chunks) => <code>{chunks}</code> })}
            </p>
            <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
              <Button type="button" onClick={() => onOpenChange(false)}>
                {t("done")}
              </Button>
            </SheetFooter>
          </div>
        ) : (
          <form onSubmit={submit} className="flex flex-1 flex-col gap-4 overflow-y-auto px-4 pb-4">
            <div className="space-y-2">
              <Label htmlFor="key-name">{t("name")}</Label>
              <Input
                id="key-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="prod-deploy-key"
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="app-slug">{t("appSlug")}</Label>
              <Input
                id="app-slug"
                value={appSlug}
                onChange={(e) => setAppSlug(e.target.value)}
                placeholder={t("appSlugPlaceholder")}
                className="font-mono text-xs"
              />
              <p className="text-muted-foreground text-xs">{t("appSlugHelp")}</p>
            </div>
            <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                {t("cancel")}
              </Button>
              <Button type="submit" disabled={generating || !name.trim()}>
                {generating ? t("generating") : t("generate")}
              </Button>
            </SheetFooter>
          </form>
        )}
      </SheetContent>
    </Sheet>
  );
}
