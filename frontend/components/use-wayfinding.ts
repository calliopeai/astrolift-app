"use client";

import { useState } from "react";

import type { WayfindingTurn } from "@/components/WayfindingBubble";

// The install's own cloud model is the default (#2138), so "not configured"
// now means the cloud could not be reached, not that a key is missing.
const NOT_CONFIGURED =
  "Wayfinding can't reach a model on this install. Ask an operator to check the platform model settings.";
const TURNED_OFF = "Wayfinding is turned off for this install.";

/** The Wayfinding conversation and its one call: the data half of WayfindingBubble. */
export function useWayfinding() {
  const [turns, setTurns] = useState<WayfindingTurn[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function ask(asked: string) {
    setPending(true);
    setError(null);
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

  return { turns, pending, error, onAsk: ask };
}
