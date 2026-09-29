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

import * as React from "react";

import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

interface NavShortcut {
  combo: string;
  label: string;
  href: string;
}

export const NAV_SHORTCUTS: NavShortcut[] = [
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
  { combo: "[", label: "Collapse or expand the main rail" },
  { combo: "]", label: "Show or hide your projects" },
];

/** The shortcut overlay, opened with `?`. Pure: its state comes from useKeyboardShortcuts. */
export function KeyboardShortcuts({
  open: overlayOpen,
  onOpenChange: setOverlayOpen,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
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
            <p className="text-muted-foreground mb-2 text-xs tracking-wide uppercase">Navigation</p>
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
            <p className="text-muted-foreground mb-2 text-xs tracking-wide uppercase">Actions</p>
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
      className="border-border bg-muted text-foreground/80 text-2xs font-mono"
    >
      {children}
    </Badge>
  );
}
