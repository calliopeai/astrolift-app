"use client";

// Read-only help bubble (#1101): "where do I ..." and "how do I ...".
//
// Global chrome rather than an entitlement-gated surface. The questions it
// answers -- where is a thing, how does a thing work -- are asked most by
// the people who have seen the least of the product, so gating it behind an
// entitlement would withhold it from exactly the operator it is for.
//
// Nothing here can mutate. The endpoint has no write path and this component
// has no mutation of its own; the most it does is render a link the person
// chooses to click. That is a property of the code, not a promise in a
// system prompt.

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { HelpCircle, Loader2, Send, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

type Turn = {
  question: string;
  answer: string;
  routes: string[];
};

// The install's own cloud model is the default (#2138), so "not configured"
// now means the cloud could not be reached, not that a key is missing.
const NOT_CONFIGURED =
  "Wayfinding can't reach a model on this install. Ask an operator to check the platform model settings.";
const TURNED_OFF = "Wayfinding is turned off for this install.";

export function WayfindingBubble() {
  const [open, setOpen] = useState(false);
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  // Newest turn into view. Scrolling the log rather than the page, so an
  // answer arriving never moves the screen underneath someone reading it.
  //
  // Feature-checked because this runs in an effect: an environment without
  // Element.scrollTo (jsdom, and anything else rendering this outside a
  // browser) would throw during commit and take the whole panel down, which
  // is a steep price for an autoscroll.
  useEffect(() => {
    const log = logRef.current;
    if (typeof log?.scrollTo === "function") log.scrollTo({ top: log.scrollHeight });
  }, [turns, pending]);

  useEffect(() => {
    if (!open) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  async function ask(event: React.FormEvent) {
    event.preventDefault();
    const asked = question.trim();
    if (!asked || pending) return;

    setPending(true);
    setError(null);
    setQuestion("");
    try {
      const res = await fetch("/api/agents/v1/wayfinding/ask/", {
        method: "POST",
        headers: { "Content-Type": "application/json", "x-platform": "web" },
        body: JSON.stringify({ question: asked }),
      });
      if (res.status === 503) {
        const body = await res.json().catch(() => null);
        setError(body?.error === "wayfinding off" ? TURNED_OFF : NOT_CONFIGURED);
        return;
      }
      if (!res.ok) {
        // The endpoint's error bodies are operator-facing prose; surfacing
        // the raw body beats "something went wrong", which tells the person
        // nothing they can act on.
        const body = await res.json().catch(() => null);
        setError(body?.error ?? `Request failed (HTTP ${res.status})`);
        return;
      }
      const json = await res.json();
      setTurns((prior) => [
        ...prior,
        { question: asked, answer: json.answer ?? "", routes: json.routes ?? [] },
      ]);
    } catch {
      setError("Couldn't reach the wayfinding service.");
    } finally {
      setPending(false);
    }
  }

  if (!open) {
    return (
      <Button
        type="button"
        size="icon"
        aria-label="Open help and wayfinding"
        onClick={() => setOpen(true)}
        className="fixed right-6 bottom-6 z-50 size-11 rounded-full shadow-lg"
      >
        <HelpCircle className="size-5" />
      </Button>
    );
  }

  return (
    <div
      role="dialog"
      aria-label="Help and wayfinding"
      className="bg-background fixed right-6 bottom-6 z-50 flex max-h-[32rem] w-[22rem] flex-col rounded-lg border shadow-xl"
    >
      <div className="flex items-center justify-between border-b px-4 py-3">
        <div>
          <p className="text-sm font-medium">Help &amp; wayfinding</p>
          <p className="text-muted-foreground text-xs">
            Finds screens and answers from the docs. It can&apos;t change anything.
          </p>
        </div>
        <Button
          type="button"
          size="icon"
          variant="ghost"
          aria-label="Close help and wayfinding"
          onClick={() => setOpen(false)}
        >
          <X className="size-4" />
        </Button>
      </div>

      <div ref={logRef} className="flex-1 overflow-y-auto px-4 py-3">
        {turns.length === 0 && !pending && !error ? (
          <p className="text-muted-foreground text-sm">
            Ask something like &ldquo;where do I add a custom domain?&rdquo; or &ldquo;how does cert
            validation work?&rdquo;
          </p>
        ) : null}

        <div className="flex flex-col gap-4">
          {turns.map((turn, index) => (
            <div key={index} className="flex flex-col gap-2">
              <p className="text-sm font-medium">{turn.question}</p>
              <p className="text-muted-foreground text-sm leading-relaxed whitespace-pre-wrap">
                {turn.answer}
              </p>
              {turn.routes.length > 0 ? (
                <div className="flex flex-wrap gap-2">
                  {turn.routes.map((route) => (
                    <Link
                      key={route}
                      href={route}
                      onClick={() => setOpen(false)}
                      className="bg-muted hover:bg-muted/70 rounded px-2 py-1 font-mono text-xs"
                    >
                      {route}
                    </Link>
                  ))}
                </div>
              ) : null}
            </div>
          ))}
        </div>

        {pending ? (
          <p className="text-muted-foreground mt-4 flex items-center gap-2 text-sm">
            <Loader2 className="size-4 animate-spin" aria-hidden />
            Looking&hellip;
          </p>
        ) : null}

        {error ? (
          <p role="alert" className="text-destructive mt-4 text-sm">
            {error}
          </p>
        ) : null}
      </div>

      <form onSubmit={ask} className="flex items-center gap-2 border-t px-4 py-3">
        <Input
          ref={inputRef}
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="Where do I…"
          maxLength={500}
          aria-label="Your question"
          disabled={pending}
        />
        <Button type="submit" size="icon" aria-label="Ask" disabled={pending || !question.trim()}>
          <Send className="size-4" />
        </Button>
      </form>
    </div>
  );
}
