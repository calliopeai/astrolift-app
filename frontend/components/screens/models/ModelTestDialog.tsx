"use client";

import { SendIcon } from "lucide-react";
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
          Test
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Test {name}</DialogTitle>
          <DialogDescription>
            Sends one bounded prompt through the cluster&apos;s keep-alive agent -- the control
            plane never reaches the model directly.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-3">
          <Textarea
            aria-label="Prompt"
            placeholder="Say hello to the model..."
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            disabled={loading}
            rows={4}
          />

          {loading && (
            <p className="text-muted-foreground text-sm" role="status">
              Waiting for the cluster agent...
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
                <Badge variant="secondary">{outcome.latencyMs ?? "?"} ms</Badge>
                <Badge variant="secondary">
                  {outcome.promptTokens ?? "?"} + {outcome.completionTokens ?? "?"} ={" "}
                  {outcome.totalTokens ?? "?"} tokens
                </Badge>
              </div>
            </div>
          )}

          {outcome?.kind === "reply" && outcome.status !== "succeeded" && (
            <p role="alert" className="text-destructive text-sm">
              {outcome.status === "timed_out"
                ? "The cluster has no agent connected, or it did not respond in time."
                : outcome.error || "The model returned an error."}
            </p>
          )}
        </div>

        <DialogFooter>
          <Button onClick={() => send(prompt)} disabled={loading || !prompt.trim()}>
            <SendIcon className="size-3.5" />
            {loading ? "Sending..." : "Send"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
