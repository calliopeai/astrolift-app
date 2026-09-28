"use client";

import { useTranslations } from "next-intl";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";

import { TRIGGER_KINDS, type TriggerKind } from "./deployments-format";
import type { useStartDeployment } from "./use-start-deployment";

export type StartDeploymentSheetProps = ReturnType<typeof useStartDeployment> & {
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

/** Start a deployment of an app image into one of its environments. */
export function StartDeploymentSheet({
  open,
  onOpenChange,
  apps,
  environments,
  appSlug,
  setAppSlug,
  submitting,
  onSubmit,
}: StartDeploymentSheetProps) {
  const t = useTranslations("lists.deployments.startSheet");
  const [environmentName, setEnvironmentName] = React.useState("");
  const [imageTag, setImageTag] = React.useState("");
  const [imageDigest, setImageDigest] = React.useState("");
  const [triggerKind, setTriggerKind] = React.useState<TriggerKind>("manual");

  React.useEffect(() => {
    if (!open) {
      setEnvironmentName("");
      setImageTag("");
      setImageDigest("");
      setTriggerKind("manual");
    }
  }, [open]);

  React.useEffect(() => {
    setEnvironmentName("");
  }, [appSlug]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!appSlug || !environmentName || !imageTag) return;
    const ok = await onSubmit({ environmentName, imageTag, imageDigest, triggerKind });
    if (ok) onOpenChange(false);
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col">
        <SheetHeader>
          <SheetTitle>{t("title")}</SheetTitle>
          <SheetDescription>{t("description")}</SheetDescription>
        </SheetHeader>
        <form onSubmit={submit} className="flex flex-1 flex-col gap-4 overflow-y-auto px-4 pb-4">
          <div className="space-y-2">
            <Label htmlFor="app">{t("appLabel")}</Label>
            <Select value={appSlug} onValueChange={setAppSlug}>
              <SelectTrigger id="app">
                <SelectValue placeholder={t("appPlaceholder")} />
              </SelectTrigger>
              <SelectContent>
                {apps.map((a) => (
                  <SelectItem key={a.id} value={a.slug}>
                    {a.name} <span className="text-muted-foreground">({a.slug})</span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label htmlFor="env">{t("envLabel")}</Label>
            <Select value={environmentName} onValueChange={setEnvironmentName} disabled={!appSlug}>
              <SelectTrigger id="env">
                <SelectValue placeholder={t("envPlaceholder")} />
              </SelectTrigger>
              <SelectContent>
                {environments.map((e) => (
                  <SelectItem key={e.id} value={e.name}>
                    {e.name}
                    {e.requiredApprovals > 0 && (
                      <span className="text-muted-foreground">
                        {" "}
                        · {t("approvalsSuffix", { count: e.requiredApprovals })}
                      </span>
                    )}
                    {e.deploysPaused && <span className="text-warning-fg"> · {t("paused")}</span>}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label htmlFor="tag">{t("tagLabel")}</Label>
            <Input
              id="tag"
              value={imageTag}
              onChange={(e) => setImageTag(e.target.value)}
              placeholder={t("tagPlaceholder")}
              className="font-mono text-xs"
              required
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="digest">{t("digestLabel")}</Label>
            <Input
              id="digest"
              value={imageDigest}
              onChange={(e) => setImageDigest(e.target.value)}
              placeholder={t("digestPlaceholder")}
              className="font-mono text-xs"
            />
            <p className="text-muted-foreground text-xs">{t("digestHint")}</p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="trigger">{t("triggerLabel")}</Label>
            <Select value={triggerKind} onValueChange={(v) => setTriggerKind(v as TriggerKind)}>
              <SelectTrigger id="trigger">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TRIGGER_KINDS.map((k) => (
                  <SelectItem key={k} value={k}>
                    {k}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              {t("cancel")}
            </Button>
            <Button
              type="submit"
              disabled={submitting || !appSlug || !environmentName || !imageTag}
            >
              {submitting ? t("submitting") : t("submit")}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
