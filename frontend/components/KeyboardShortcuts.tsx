"use client";

/**
 * Global keyboard-shortcut layer + discoverable overlay (#701).
 *
 * Operators who live in this UI need vim-style nav. We already had
 * `cmd-k` (CommandPalette); this component adds the rest of the
 * shortcuts and a `?` overlay that lists them all.
 *
 * Shortcut taxonomy:
 *   - Single-key navigation prefix:  g  (then second key picks destination)
 *   - One-shot:                       ?  (overlay)  ·  /  (focus first input)
 *   - Modifier shortcuts:             cmd-k (palette, wired in CommandPalette.tsx)
 *
 * Skip handler when the user is mid-typing in an input/textarea so
 * we don't hijack form interaction. The handler also ignores any
 * key combination using a modifier (cmd / ctrl / alt) to leave the
 * platform's chord shortcuts (cmd-k, cmd-/) alone.
 */

import { useRouter } from "next/navigation";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

// Sequences trigger after the user types `g` then a destination
// key within G_SEQUENCE_WINDOW_MS. After the window expires the
// state resets so `g` typed alone in a text field doesn't get
// captured later.
const G_SEQUENCE_WINDOW_MS = 1500;

interface NavShortcut {
  combo: string;
  label: string;
  href: string;
}

const NAV_SHORTCUTS: NavShortcut[] = [
  { combo: "g a", label: "Apps", href: "/apps" },
  { combo: "g o", label: "Operations", href: "/ops" },
  { combo: "g d", label: "Deployments", href: "/deployments" },
  { combo: "g p", label: "Previews", href: "/previews" },
  { combo: "g c", label: "Clusters", href: "/clusters" },
  { combo: "g j", label: "Jobs", href: "/jobs" },
  { combo: "g t", label: "Teams", href: "/administration/teams" },
  { combo: "g s", label: "Settings", href: "/administration/organization" },
];

interface OneShotShortcut {
  combo: string;
  label: string;
}

const ONE_SHOT_SHORTCUTS: OneShotShortcut[] = [
  { combo: "?", label: "Show this overlay" },
  { combo: "/", label: "Focus the first input on the page" },
  { combo: "⌘ K", label: "Open command palette (also Ctrl-K)" },
  { combo: "Esc", label: "Close any open overlay / drawer" },
];

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
}

export function KeyboardShortcuts() {
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
          "input:not([type=hidden]):not([disabled])",
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

  return (
    <Dialog open={overlayOpen} onOpenChange={setOverlayOpen}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Keyboard shortcuts</DialogTitle>
          <DialogDescription>
            Discoverable from anywhere by pressing <Kbd>?</Kbd>.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <section>
            <p className="text-muted-foreground mb-2 text-xs tracking-wide uppercase">
              Navigation
            </p>
            <ul className="space-y-1.5 text-sm">
              {NAV_SHORTCUTS.map((s) => (
                <li key={s.combo} className="flex items-center justify-between">
                  <span>{s.label}</span>
                  <Kbd>{s.combo}</Kbd>
                </li>
              ))}
            </ul>
          </section>
          <section>
            <p className="text-muted-foreground mb-2 text-xs tracking-wide uppercase">
              Actions
            </p>
            <ul className="space-y-1.5 text-sm">
              {ONE_SHOT_SHORTCUTS.map((s) => (
                <li key={s.combo} className="flex items-center justify-between">
                  <span>{s.label}</span>
                  <Kbd>{s.combo}</Kbd>
                </li>
              ))}
            </ul>
          </section>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <Badge
      variant="outline"
      className="border-border bg-muted text-foreground/80 font-mono text-2xs"
    >
      {children}
    </Badge>
  );
}
