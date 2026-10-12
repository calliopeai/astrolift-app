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

import { useTranslations } from "next-intl";
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
  /** `keyboardShortcuts.nav` key. */
  labelKey: string;
  href: string;
}

export const NAV_SHORTCUTS: NavShortcut[] = [
  { combo: "g a", labelKey: "apps", href: "/apps" },
  { combo: "g o", labelKey: "operations", href: "/ops" },
  { combo: "g d", labelKey: "deployments", href: "/deployments" },
  { combo: "g p", labelKey: "previews", href: "/previews" },
  { combo: "g c", labelKey: "clusters", href: "/clusters" },
  { combo: "g j", labelKey: "jobs", href: "/jobs" },
  { combo: "g t", labelKey: "teams", href: "/administration/teams" },
  { combo: "g s", labelKey: "settings", href: "/administration/organization" },
];

interface OneShotShortcut {
  combo: string;
  /** `keyboardShortcuts.actions` key. */
  labelKey: string;
}

const ONE_SHOT_SHORTCUTS: OneShotShortcut[] = [
  { combo: "?", labelKey: "overlay" },
  { combo: "/", labelKey: "focusInput" },
  { combo: "⌘ K", labelKey: "palette" },
  { combo: "Esc", labelKey: "close" },
  { combo: "[", labelKey: "rail" },
  { combo: "]", labelKey: "projects" },
];

/** The shortcut overlay, opened with `?`. Pure: its state comes from useKeyboardShortcuts. */
export function KeyboardShortcuts({
  open: overlayOpen,
  onOpenChange: setOverlayOpen,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const t = useTranslations("keyboardShortcuts");
  return (
    <Dialog open={overlayOpen} onOpenChange={setOverlayOpen}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{t("title")}</DialogTitle>
          <DialogDescription>
            {t.rich("description", { kbd: (chunks) => <Kbd>{chunks}</Kbd> })}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <section>
            <p className="text-muted-foreground mb-2 text-xs tracking-wide uppercase">
              {t("navigation")}
            </p>
            <ul className="space-y-1.5 text-sm">
              {NAV_SHORTCUTS.map((s) => (
                <li key={s.combo} className="flex items-center justify-between">
                  <span>{t(`nav.${s.labelKey}`)}</span>
                  <Kbd>{s.combo}</Kbd>
                </li>
              ))}
            </ul>
          </section>
          <section>
            <p className="text-muted-foreground mb-2 text-xs tracking-wide uppercase">
              {t("actionsHeading")}
            </p>
            <ul className="space-y-1.5 text-sm">
              {ONE_SHOT_SHORTCUTS.map((s) => (
                <li key={s.combo} className="flex items-center justify-between">
                  <span>{t(`actions.${s.labelKey}`)}</span>
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
