"use client";

import { KeyRoundIcon, UserIcon, UsersIcon, UsersRoundIcon, XIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { cn } from "@/lib/utils";

import { type Principal, type PrincipalKind } from "./access-model";

const ICON: Record<PrincipalKind, typeof UserIcon> = {
  user: UserIcon,
  group: UsersRoundIcon,
  team: UsersIcon,
  token: KeyRoundIcon,
};

export interface PrincipalChipProps {
  principal: Principal;
  /** `inline` sits in a sentence or a table cell; `block` adds the detail line. */
  variant?: "inline" | "block";
  /** The mono id after the name. On by default; off where the id is the name. */
  showId?: boolean;
  /** Renders a remove button (a pick in a multi-select). */
  onRemove?: () => void;
  className?: string;
}

/**
 * Who holds access (design 4): a user, an IdP group, a team or a token, told
 * apart by icon and noun, named, with its stable id in mono. Links to the
 * principal's page when it has one. Overflow-safe: the name truncates, the
 * id never widens the row.
 */
export function PrincipalChip({
  principal: p,
  variant = "inline",
  showId = true,
  onRemove,
  className,
}: PrincipalChipProps) {
  const t = useTranslations("shared.access");
  const Icon = ICON[p.kind];
  const noun = t(`principal.${p.kind}`);
  const idShown = showId && p.id !== p.name;

  const body = (
    <>
      <Icon aria-hidden className="text-muted-foreground size-3.5 shrink-0" />
      <span className="sr-only">{noun} </span>
      <span className="flex min-w-0 flex-col">
        <span className="flex min-w-0 items-baseline gap-1.5">
          <span className="min-w-0 truncate font-medium" title={p.name}>
            {p.name}
          </span>
          {idShown && (
            <span
              className="text-muted-foreground text-2xs min-w-0 truncate font-mono"
              title={p.id}
            >
              {p.id}
            </span>
          )}
        </span>
        {variant === "block" && p.detail && (
          <span className="text-muted-foreground min-w-0 truncate text-xs" title={p.detail}>
            {p.detail}
          </span>
        )}
      </span>
    </>
  );

  const frame = cn(
    "inline-flex max-w-full min-w-0 items-center gap-1.5 text-sm",
    onRemove && "bg-muted/60 rounded-full border py-0.5 pr-1 pl-2",
    className
  );

  return (
    <span className={frame} data-principal-kind={p.kind}>
      {p.href ? (
        <Link
          href={p.href}
          className="focus-visible:ring-ring inline-flex min-w-0 items-center gap-1.5 rounded-sm hover:underline focus-visible:ring-2 focus-visible:outline-none"
        >
          {body}
        </Link>
      ) : (
        <span className="inline-flex min-w-0 items-center gap-1.5">{body}</span>
      )}
      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          aria-label={t("remove", { kind: noun, name: p.name })}
          className="text-muted-foreground hover:text-foreground focus-visible:ring-ring shrink-0 rounded-full p-0.5 focus-visible:ring-2 focus-visible:outline-none"
        >
          <XIcon className="size-3" />
        </button>
      )}
    </span>
  );
}
