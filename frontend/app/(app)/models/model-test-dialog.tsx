"use client";

import { useMutation } from "@apollo/client/react";
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
import { TEST_MODEL_ENDPOINT } from "@/graphql/services/services.mutations";

// TEST_MODEL_ENDPOINT lives in services.mutations.ts, which interpolates a
// plain field-list constant elsewhere in the file and so is excluded from
// codegen wholesale (see codegen.ts) -- same reason ModelReplicas and
// DeployModelSheet hand-declare their own result shape.
interface TestModelEndpointResult {
  testModelEndpoint: {
    ok: boolean;
    errors?: { message: string }[] | null;
    data?: {
      status: "succeeded" | "failed" | "timed_out";
      reply: string;
      latencyMs: number | null;
      promptTokens: number | null;
      completionTokens: number | null;
      totalTokens: number | null;
      error: string;
    } | null;
  };
}

type Outcome =
  | { kind: "error"; message: string }
  | {
      kind: "reply";
      status: "succeeded" | "failed" | "timed_out";
      reply: string;
      latencyMs: number | null;
      promptTokens: number | null;
      completionTokens: number | null;
      totalTokens: number | null;
      error: string;
    };

export function ModelTestDialog({ id, name }: { id: string; name: string }) {
  const [open, setOpen] = React.useState(false);
  const [prompt, setPrompt] = React.useState("");
  const [outcome, setOutcome] = React.useState<Outcome | null>(null);
  const [test, { loading }] = useMutation<TestModelEndpointResult>(TEST_MODEL_ENDPOINT);

  async function send() {
    setOutcome(null);
    const { data } = await test({ variables: { input: { managedServiceId: id, prompt } } });
    const result = data?.testModelEndpoint;
    if (!result?.ok) {
      setOutcome({ kind: "error", message: result?.errors?.[0]?.message ?? "Test failed" });
      return;
    }
    if (result.data) {
      setOutcome({ kind: "reply", ...result.data });
    }
  }

  function onOpenChange(next: boolean) {
    setOpen(next);
    if (!next) {
      setOutcome(null);
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
          <Button onClick={send} disabled={loading || !prompt.trim()}>
            <SendIcon className="size-3.5" />
            {loading ? "Sending..." : "Send"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
