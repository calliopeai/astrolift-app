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
import { Textarea } from "@/components/ui/textarea";

import type { CreateAlertRuleInput } from "./use-alerts";

const TARGETS = ["app", "env", "workload", "global"];
const SEVERITIES = ["info", "warn", "critical"];

export interface CreateAlertRuleSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (input: CreateAlertRuleInput) => Promise<boolean>;
  busy: boolean;
}

/** New alert rule form; resets every field when it closes. */
export function CreateAlertRuleSheet({
  open,
  onOpenChange,
  onSubmit,
  busy,
}: CreateAlertRuleSheetProps) {
  const t = useTranslations("lists.alerts.create");
  const [name, setName] = React.useState("");
  const [target, setTarget] = React.useState("global");
  const [targetId, setTargetId] = React.useState("");
  const [severity, setSeverity] = React.useState("warn");
  const [predicateText, setPredicateText] = React.useState(
    JSON.stringify({ event: "deployment.failed" }, null, 2)
  );
  const [channelsText, setChannelsText] = React.useState(
    JSON.stringify({ slack: "#oncall" }, null, 2)
  );
  const [predicateError, setPredicateError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!open) {
      setName("");
      setTarget("global");
      setTargetId("");
      setSeverity("warn");
      setPredicateText(JSON.stringify({ event: "deployment.failed" }, null, 2));
      setChannelsText(JSON.stringify({ slack: "#oncall" }, null, 2));
      setPredicateError(null);
    }
  }, [open]);

  const isGlobal = target === "global";

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col sm:max-w-xl">
        <SheetHeader>
          <SheetTitle>{t("title")}</SheetTitle>
          <SheetDescription>{t("description")}</SheetDescription>
        </SheetHeader>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            if (!name.trim()) return;
            let predicate: Record<string, unknown> | null = null;
            let channels: Record<string, unknown> | null = null;
            try {
              predicate = predicateText.trim() ? JSON.parse(predicateText) : null;
              channels = channelsText.trim() ? JSON.parse(channelsText) : null;
              setPredicateError(null);
            } catch (err) {
              setPredicateError(String(err));
              return;
            }
            await onSubmit({
              name: name.trim(),
              target,
              targetId: isGlobal ? null : targetId.trim() || null,
              severity,
              predicate,
              notifyChannels: channels,
            });
          }}
          className="flex flex-1 flex-col gap-4 overflow-auto px-4 pb-4"
        >
          <div className="space-y-2">
            <Label htmlFor="ar-name">{t("nameLabel")}</Label>
            <Input
              id="ar-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t("namePlaceholder")}
              autoFocus
              required
              spellCheck={false}
              className="font-mono"
            />
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="ar-target">{t("targetLabel")}</Label>
              <Select value={target} onValueChange={setTarget}>
                <SelectTrigger id="ar-target">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {TARGETS.map((tgt) => (
                    <SelectItem key={tgt} value={tgt}>
                      {tgt}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="ar-severity">{t("severityLabel")}</Label>
              <Select value={severity} onValueChange={setSeverity}>
                <SelectTrigger id="ar-severity">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {SEVERITIES.map((s) => (
                    <SelectItem key={s} value={s}>
                      {s}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="space-y-2">
            <Label htmlFor="ar-target-id">{t("targetIdLabel")}</Label>
            <Input
              id="ar-target-id"
              value={targetId}
              onChange={(e) => setTargetId(e.target.value)}
              placeholder={isGlobal ? t("targetIdGlobal") : t("targetIdPlaceholder")}
              disabled={isGlobal}
              spellCheck={false}
              className="font-mono"
            />
            <p className="text-muted-foreground text-xs">
              {isGlobal ? t("targetIdGlobalHint") : t("targetIdHint")}
            </p>
          </div>
          <div className="space-y-2">
            <Label htmlFor="ar-predicate">{t("predicateLabel")}</Label>
            <Textarea
              id="ar-predicate"
              value={predicateText}
              onChange={(e) => setPredicateText(e.target.value)}
              rows={6}
              spellCheck={false}
              className="font-mono text-xs"
            />
            {predicateError && <p className="text-destructive text-xs">{predicateError}</p>}
          </div>
          <div className="space-y-2">
            <Label htmlFor="ar-channels">{t("channelsLabel")}</Label>
            <Textarea
              id="ar-channels"
              value={channelsText}
              onChange={(e) => setChannelsText(e.target.value)}
              rows={4}
              spellCheck={false}
              className="font-mono text-xs"
            />
          </div>
          <SheetFooter className="flex-row justify-end gap-2 px-0">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              {t("cancel")}
            </Button>
            <Button type="submit" disabled={busy || !name.trim()}>
              {busy ? t("submitting") : t("submit")}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
