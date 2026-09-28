"use client";

import { CheckIcon, CopyIcon } from "lucide-react";
import * as React from "react";

import { useCopyToClipboard } from "@/hooks/use-copy-to-clipboard";
import { cn } from "@/lib/utils";

/**
 * A SHA, digest, ARN, id or URL, shown one way everywhere (spec 44 §7, #2125).
 *
 * Short in titles, tables and dialogs, where a 40-character SHA or a 90-char
 * ARN is what widened frames past their border; the full value is in the
 * tooltip and one click copies it. `full` is the body form: every character,
 * wrapping anywhere, with the same copy control.
 */

export type IdentifierKind = "sha" | "digest" | "arn" | "id" | "url";

const SHA_CHARS = 8;
const ID_CHARS = 8;
const DIGEST_CHARS = 12;

/** The short form of `value`, by kind. Pure, so lists and tests agree. */
export function shortIdentifier(value: string, kind: IdentifierKind): string {
  switch (kind) {
    case "sha":
      return value.length > SHA_CHARS ? value.slice(0, SHA_CHARS) : value;
    case "digest": {
      // sha256:<hex> keeps its algorithm, which is the useful part to read.
      const [algo, hex] = value.includes(":") ? value.split(":", 2) : ["", value];
      const short = hex.length > DIGEST_CHARS ? `${hex.slice(0, DIGEST_CHARS)}…` : hex;
      return algo ? `${algo}:${short}` : short;
    }
    case "arn": {
      // arn:partition:service:region:account:resource → service … resource,
      // the two parts that say what it is.
      const parts = value.split(":");
      if (parts.length < 6 || parts[0] !== "arn") return value;
      const resource = parts.slice(5).join(":");
      if (resource.length <= 40) return `${parts[2]}:…:${resource}`;
      // Keep the resource type whole (certificate/, role/, loadbalancer/app/)
      // and shorten only the id after it.
      const cut = Math.max(resource.lastIndexOf("/"), resource.lastIndexOf(":"));
      const type = cut > 0 ? resource.slice(0, cut + 1) : "";
      const id = cut > 0 ? resource.slice(cut + 1) : resource;
      return `${parts[2]}:…:${type}${id.length > 12 ? `…${id.slice(-12)}` : id}`;
    }
    case "url":
      try {
        const url = new URL(value);
        const path = url.pathname === "/" ? "" : url.pathname;
        const shown = `${url.host}${path}`;
        return shown.length > 48 ? `${shown.slice(0, 47)}…` : shown;
      } catch {
        return value;
      }
    case "id":
    default:
      return value.length > ID_CHARS + 4 ? value.slice(0, ID_CHARS) : value;
  }
}

export interface IdentifierProps {
  value: string;
  kind?: IdentifierKind;
  /** `short` (default) for titles and tables; `full` for the body of a page or dialog. */
  form?: "short" | "full";
  /** Copy on click. On by default: an identifier is shown to be pasted. */
  copyable?: boolean;
  className?: string;
}

const COPY_RESET_MS = 2000;

export function Identifier({
  value,
  kind = "id",
  form = "short",
  copyable = true,
  className,
}: IdentifierProps) {
  const [copied, copy] = useCopyToClipboard(COPY_RESET_MS);
  const shown = form === "full" ? value : shortIdentifier(value, kind);
  const text = (
    <span
      className={cn(
        "min-w-0 font-mono",
        form === "full" ? "[overflow-wrap:anywhere]" : "truncate whitespace-nowrap"
      )}
    >
      {shown}
    </span>
  );

  if (!copyable) {
    return (
      <span title={value} className={cn("inline-flex max-w-full min-w-0", className)}>
        {text}
      </span>
    );
  }
  const Icon = copied ? CheckIcon : CopyIcon;
  return (
    <button
      type="button"
      title={value}
      aria-label={`Copy ${value}`}
      onClick={() => copy(value)}
      className={cn(
        "hover:text-foreground focus-visible:ring-ring inline-flex max-w-full min-w-0 items-center gap-1 rounded-sm text-left focus-visible:ring-2 focus-visible:outline-none",
        form === "full" && "items-start",
        className
      )}
    >
      {text}
      <Icon aria-hidden className={cn("size-3 shrink-0 opacity-60", form === "full" && "mt-1")} />
      <span className="sr-only" aria-live="polite">
        {copied ? "Copied" : ""}
      </span>
    </button>
  );
}
