"use client";

import { useTranslations } from "next-intl";
import NextLink from "next/link";

import { Badge } from "@/components/ui/badge";

import { resolveSourceHref } from "./event-source";

/**
 * An event's source as a badge, linked to the resource when
 * `resolveSourceHref` knows where it lives, text-only otherwise.
 */
export function SourceBadge({
  resourceKind,
  resourceId,
  payload,
}: {
  resourceKind: string;
  resourceId: string;
  payload: Record<string, unknown> | null | undefined;
}) {
  const t = useTranslations("lists.events");
  if (!resourceKind && !resourceId) return null;
  const href = resolveSourceHref(resourceKind, resourceId, payload ?? {});
  const label = resourceId || resourceKind;
  const display = resourceKind ? `${resourceKind}:${resourceId || "?"}` : (label ?? "");
  if (!href) {
    return (
      <Badge variant="outline" className="font-mono text-xs">
        {display}
      </Badge>
    );
  }
  return (
    <NextLink
      href={href}
      onClick={(ev) => ev.stopPropagation()}
      className="inline-flex items-center"
      aria-label={`${t("sourceLabel")}: ${display}`}
    >
      <Badge variant="secondary" className="hover:bg-primary/10 cursor-pointer font-mono text-xs">
        {display}
      </Badge>
    </NextLink>
  );
}

/** One-line payload preview; the full document lives on the detail page. */
export function PayloadPreview({
  payload,
}: {
  payload: Record<string, unknown> | null | undefined;
}) {
  if (Object.keys(payload ?? {}).length === 0) {
    return <span className="text-muted-foreground text-xs">—</span>;
  }
  const text = JSON.stringify(payload);
  return (
    <span
      className="text-muted-foreground text-2xs block max-w-md truncate font-mono"
      title={text.slice(0, 500)}
    >
      {text}
    </span>
  );
}
