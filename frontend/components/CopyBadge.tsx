"use client";

import { CheckIcon, CopyIcon } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { useCopyToClipboard } from "@/hooks/use-copy-to-clipboard";
import { cn } from "@/lib/utils";

/**
 * Reusable click-to-copy badge with a transient "Copied!" affordance
 * (#431).
 *
 * Renders ``label`` (or ``value`` when no label is passed) inside a
 * pill-shaped clickable surface. The copy / check icon swaps for two
 * seconds after a click and a Sonner toast confirms — the icon swap
 * covers the case where the toast is dismissed and the toast covers
 * the case where the icon is off-screen.
 *
 * The whole badge is the click target, not just the icon — operators
 * read the value first, then copy it, so the largest possible hit
 * area beats a tiny icon button.
 *
 * Used in the previews list to surface preview hostnames (#431) but
 * the component is intentionally generic — any short string operators
 * paste into terminals (cluster slug, namespace, certificate
 * thumbprint, etc.) should use this rather than reinvent the copy
 * affordance.
 */
export interface CopyBadgeProps {
  value: string;
  /** Optional rendered text — defaults to ``value``. Lets callers
   * show a truncated label (``"pr-42-…"``) while copying the full
   * value. */
  label?: React.ReactNode;
  /** Toast message after successful copy. Defaults to "Copied!" */
  toastMessage?: string;
  /** Title attribute for the badge — defaults to ``"Copy <value>"``. */
  title?: string;
  className?: string;
  /** When set, renders a separate "Open in new tab" arrow icon next
   * to the badge that links to ``openHref``. Used for preview hostnames
   * so the URL is one click away from the copy affordance. */
  openHref?: string;
  /** Aria label for the open link. Defaults to "Open in new tab". */
  openLabel?: string;
}

const COPY_RESET_MS = 2000;

export function CopyBadge({
  value,
  label,
  toastMessage = "Copied!",
  title,
  className,
  openHref,
  openLabel = "Open in new tab",
}: CopyBadgeProps) {
  const [copied, copy] = useCopyToClipboard(COPY_RESET_MS);

  const handleCopy = React.useCallback(() => {
    copy(value);
    toast.success(toastMessage);
  }, [copy, toastMessage, value]);

  return (
    <span className={cn("inline-flex items-center gap-1", className)}>
      <button
        type="button"
        onClick={handleCopy}
        title={title ?? `Copy ${value}`}
        className={cn(
          "bg-background inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5",
          "hover:bg-muted/60 font-mono text-sm transition-colors",
          "focus-visible:ring-ring focus-visible:ring-2 focus-visible:outline-none"
        )}
      >
        <span className="truncate">{label ?? value}</span>
        {copied ? (
          <CheckIcon className="size-3 text-emerald-600 dark:text-emerald-400" aria-hidden />
        ) : (
          <CopyIcon className="text-muted-foreground size-3" aria-hidden />
        )}
      </button>
      {openHref && (
        <Button
          asChild
          variant="ghost"
          size="icon"
          className="text-muted-foreground hover:text-foreground size-6"
        >
          <a
            href={openHref}
            target="_blank"
            rel="noreferrer"
            aria-label={openLabel}
            title={openLabel}
          >
            <ExternalArrow />
          </a>
        </Button>
      )}
    </span>
  );
}

function ExternalArrow() {
  // Inline so the import isn't an extra round-trip through lucide-react
  // for what is a small constant svg. Matches the visual weight of the
  // ExternalLinkIcon elsewhere in the previews UI.
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M7 17 17 7" />
      <path d="M7 7h10v10" />
    </svg>
  );
}
