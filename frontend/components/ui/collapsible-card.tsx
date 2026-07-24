"use client";

import { ChevronDownIcon } from "lucide-react";
import * as React from "react";

import { Card } from "@/components/ui/card";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";

const STORAGE_PREFIX = "astrolift.collapsible.";

function loadOpen(storageKey: string | undefined, fallback: boolean): boolean {
  if (!storageKey || typeof window === "undefined") return fallback;
  try {
    const raw = window.localStorage.getItem(STORAGE_PREFIX + storageKey);
    return raw === null ? fallback : raw === "1";
  } catch {
    return fallback;
  }
}

function saveOpen(storageKey: string | undefined, open: boolean) {
  if (!storageKey || typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_PREFIX + storageKey, open ? "1" : "0");
  } catch {
    // localStorage may be disabled (private mode, quota) — degrade silently.
  }
}

interface CollapsibleCardProps {
  /** Header content — the whole header row is the collapse toggle. */
  title: React.ReactNode;
  /**
   * Persists open/closed across reloads under
   * `astrolift.collapsible.<storageKey>`. Omit for ephemeral state.
   */
  storageKey?: string;
  defaultOpen?: boolean;
  /**
   * Right-aligned header controls rendered OUTSIDE the toggle button, so
   * clicking them doesn't collapse the card (e.g. a copy / maximize action).
   */
  actions?: React.ReactNode;
  className?: string;
  contentClassName?: string;
  children: React.ReactNode;
}

/**
 * A `Card` whose body collapses to just its header. Lets an operator maximize
 * one panel by folding the others — the run-detail Overview/Result/Logs pattern
 * (#1105) — with the open/closed choice persisted per `storageKey`.
 *
 * Built on the shared radix `Collapsible`; the header row is the trigger, with
 * an optional non-toggling `actions` slot on the right.
 */
export function CollapsibleCard({
  title,
  storageKey,
  defaultOpen = true,
  actions,
  className,
  contentClassName,
  children,
}: CollapsibleCardProps) {
  const [open, setOpen] = React.useState(defaultOpen);
  // Hydrate from storage after mount so SSR and the first client render agree.
  React.useEffect(() => {
    setOpen(loadOpen(storageKey, defaultOpen));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storageKey]);

  function handleChange(next: boolean) {
    setOpen(next);
    saveOpen(storageKey, next);
  }

  return (
    <Collapsible open={open} onOpenChange={handleChange} asChild>
      <Card className={cn("gap-0 py-0", className)}>
        <div className="flex items-center gap-2 px-4 py-3">
          <CollapsibleTrigger className="group/cc -mx-1 flex min-w-0 flex-1 items-center gap-2 rounded-md px-1 py-0.5 text-left transition-colors hover:bg-muted/40">
            <ChevronDownIcon className="text-muted-foreground size-4 shrink-0 transition-transform group-data-[state=closed]/cc:-rotate-90" />
            <div className="text-base font-medium leading-snug min-w-0">{title}</div>
          </CollapsibleTrigger>
          {actions && <div className="flex shrink-0 items-center gap-1">{actions}</div>}
        </div>
        <CollapsibleContent>
          <div className={cn("px-4 pb-4", contentClassName)}>{children}</div>
        </CollapsibleContent>
      </Card>
    </Collapsible>
  );
}
