"use client";

import { useRouter } from "next/navigation";
import * as React from "react";

import { NAV_SHORTCUTS } from "@/components/KeyboardShortcuts";

// Sequences trigger after the user types `g` then a destination
// key within G_SEQUENCE_WINDOW_MS. After the window expires the
// state resets so `g` typed alone in a text field doesn't get
// captured later.
const G_SEQUENCE_WINDOW_MS = 1500;

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
}

/** The bare-key shortcuts (`?`, `/`, `g` then a key); the data half of KeyboardShortcuts. */
export function useKeyboardShortcuts() {
  const router = useRouter();
  const [overlayOpen, setOverlayOpen] = React.useState(false);
  const gSequenceUntilRef = React.useRef<number>(0);

  React.useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (isTypingTarget(e.target)) return;
      // Leave Cmd / Ctrl / Alt combos alone — those are owned by
      // other handlers (CommandPalette uses Cmd-K, browser owns most
      // others). We only want bare-key shortcuts here.
      if (e.metaKey || e.ctrlKey || e.altKey) return;

      // `?` (shift + /) — both forms forwarded by browsers depending
      // on layout. Open the overlay.
      if (e.key === "?" || (e.shiftKey && e.key === "/")) {
        e.preventDefault();
        setOverlayOpen((v) => !v);
        return;
      }

      // `/` alone — focus the first input. Skipped if the user
      // already has focus inside an input (handled above).
      if (e.key === "/") {
        const firstInput = document.querySelector<HTMLElement>(
          "input:not([type=hidden]):not([disabled])"
        );
        if (firstInput) {
          e.preventDefault();
          firstInput.focus();
        }
        return;
      }

      // `g` starts a sequence; `g` then a destination key navigates.
      const now = Date.now();
      if (now < gSequenceUntilRef.current) {
        // Inside the g-prefix window — try to match the second key
        // against NAV_SHORTCUTS.
        gSequenceUntilRef.current = 0;
        const match = NAV_SHORTCUTS.find((s) => s.combo === `g ${e.key}`);
        if (match) {
          e.preventDefault();
          router.push(match.href);
        }
        return;
      }
      if (e.key === "g") {
        gSequenceUntilRef.current = now + G_SEQUENCE_WINDOW_MS;
        return;
      }
    }

    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [router]);

  return { open: overlayOpen, onOpenChange: setOverlayOpen };
}
