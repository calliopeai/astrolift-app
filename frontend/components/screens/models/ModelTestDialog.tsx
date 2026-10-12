"use client";

import { SendIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";

import type { useModelTest } from "./use-model-test";

export type ModelTestDialogViewProps = ReturnType<typeof useModelTest> & {
  /** Start open (stories); the app opens it from the Test button. */
  defaultOpen?: boolean;
};

/** Test button and dialog that sends one prompt to a hosted model. */
export function ModelTestDialogView({
  name,
  loading,
  outcome,
  send,
  reset,
  defaultOpen = false,
}: ModelTestDialogViewProps) {
  const t = useTranslations("modelTest");
  const [open, setOpen] = React.useState(defaultOpen);
  const [prompt, setPrompt] = React.useState("");

  function onOpenChange(next: boolean) {
    setOpen(next);
    if (!next) {
      reset();
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogTrigger asChild>
        <Button size="sm" variant="outline">
          {t("test")}
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("title", { name })}</DialogTitle>
          <DialogDescription>{t("description")}</DialogDescription>
        </DialogHeader>

        <div className="grid gap-3">
          <Textarea
            aria-label={t("prompt")}
            placeholder={t("placeholder")}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            disabled={loading}
            rows={4}
          />

          {loading && (
            <p className="text-muted-foreground text-sm" role="status">
              {t("waiting")}
            </p>
          )}

          {outcome?.kind === "error" && (
            <p role="alert" className="text-destructive text-sm">
              {outcome.message}
            </p>
          )}

          {outcome?.kind === "reply" && outcome.status === "succeeded" && (
            <div className="grid gap-2 rounded-md border p-3">
              <p className="text-sm whitespace-pre-wrap">{outcome.reply}</p>
              <div className="flex flex-wrap gap-2">
                <Badge variant="secondary">{t("latency", { ms: outcome.latencyMs ?? "?" })}</Badge>
                <Badge variant="secondary">
                  {t("tokens", {
                    prompt: outcome.promptTokens ?? "?",
                    completion: outcome.completionTokens ?? "?",
                    total: outcome.totalTokens ?? "?",
                  })}
                </Badge>
              </div>
            </div>
          )}

          {outcome?.kind === "reply" && outcome.status !== "succeeded" && (
            <p role="alert" className="text-destructive text-sm">
              {outcome.status === "timed_out" ? t("timedOut") : outcome.error || t("modelError")}
            </p>
          )}
        </div>

        <DialogFooter>
          <Button onClick={() => send(prompt)} disabled={loading || !prompt.trim()}>
            <SendIcon className="size-3.5" />
            {loading ? t("sending") : t("send")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
