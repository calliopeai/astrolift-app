"use client";

import { useTranslations } from "next-intl";
import NextLink from "next/link";

import { Badge } from "@/components/ui/badge";

import { eventPayloadText, resolveSourceHref } from "./event-source";

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
  payload: unknown;
}) {
  const t = useTranslations("lists.events");
  if (!resourceKind && !resourceId) return null;
  const href = resolveSourceHref(resourceKind, resourceId, payload ?? {});
  const label = resourceId || resourceKind;
  const display = resourceKind ? `${resourceKind}:${resourceId || "?"}` : (label ?? "");
  if (!href) {
    return (
      <Badge variant="outline" className="max-w-full shrink font-mono text-xs">
        <span className="min-w-0 truncate" title={display}>
          {display}
        </span>
      </Badge>
    );
  }
  return (
    <NextLink
      href={href}
      onClick={(ev) => ev.stopPropagation()}
      className="inline-flex max-w-full min-w-0 items-center"
      aria-label={`${t("sourceLabel")}: ${display}`}
    >
      <Badge
        variant="secondary"
        className="hover:bg-primary/10 max-w-full shrink cursor-pointer font-mono text-xs"
      >
        <span className="min-w-0 truncate" title={display}>
          {display}
        </span>
      </Badge>
    </NextLink>
  );
}

/** One-line payload preview; the full document lives on the detail page. */
export function PayloadPreview({ payload }: { payload: unknown }) {
  const text = eventPayloadText(payload);
  if (text === null) {
    return <span className="text-muted-foreground text-xs">—</span>;
  }
  return (
    <span
      className="text-muted-foreground text-2xs block max-w-md truncate font-mono"
      title={text.slice(0, 500)}
    >
      {text}
    </span>
  );
}
