"use client";

import { ArrowUpRightIcon, CornerLeftUpIcon, UserCheckIcon, UsersRoundIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import type * as React from "react";

import { cn } from "@/lib/utils";

import { describeSource, type GrantSourceInfo, viaName } from "./access-model";

export interface GrantSourceProps {
  source: GrantSourceInfo;
  className?: string;
}

/**
 * Why a principal has a grant where it is being looked at (design 3.2, 3.3):
 * `direct`, `via group okta:eng`, `via team payments`, `inherited from org
 * acme`, or a via and an inheritance together. Each part links to where the
 * grant can be changed, because that is the only place it can be removed.
 * Each part reads as its own phrase, so a screen reader hears the same words.
 */
export function GrantSource({ source, className }: GrantSourceProps) {
  const t = useTranslations("shared.access");
  const { via, inheritedFrom } = source;
  const direct = !via && !inheritedFrom;

  return (
    <span
      className={cn("inline-flex max-w-full min-w-0 flex-wrap items-center gap-1", className)}
      title={describeSource(source, {
        direct: t("direct"),
        via: (kind, name) => t("via", { kind: t(`principal.${kind}`), name }),
        inherited: (kind, name) => t("inherited", { kind: t(`scope.${kind}`), name }),
        combined: (via, inherited) => t("combined", { via, inherited }),
      })}
      data-source={
        direct ? "direct" : [via?.kind, inheritedFrom && "inherited"].filter(Boolean).join(" ")
      }
    >
      {direct && (
        <Part icon={<UserCheckIcon className="size-3" />} tone="direct">
          {t("direct")}
        </Part>
      )}
      {via && (
        <Part icon={<UsersRoundIcon className="size-3" />} href={via.href} tone="via">
          {t.rich("viaRich", {
            kind: t(`principal.${via.kind}`),
            identifier: (chunks) => <span className="font-mono">{chunks}</span>,
            name: viaName(via),
          })}
        </Part>
      )}
      {inheritedFrom && (
        <Part
          icon={<CornerLeftUpIcon className="size-3" />}
          href={inheritedFrom.href}
          tone="inherited"
        >
          {t.rich("inheritedRich", {
            kind: t(`scope.${inheritedFrom.kind}`),
            name: inheritedFrom.name,
            identifier: (chunks) => <span className="font-mono">{chunks}</span>,
          })}
        </Part>
      )}
    </span>
  );
}

const TONE = {
  direct: "border-border text-foreground",
  via: "border-info-border bg-info-bg text-info-fg",
  inherited: "border-border bg-muted/50 text-muted-foreground",
} as const;

function Part({
  icon,
  href,
  tone,
  children,
}: {
  icon: React.ReactNode;
  href?: string;
  tone: keyof typeof TONE;
  children: React.ReactNode;
}) {
  const chip = cn(
    "inline-flex max-w-full min-w-0 items-center gap-1 rounded-full border px-2 py-0.5 text-2xs",
    TONE[tone]
  );
  const label = (
    <>
      <span aria-hidden className="shrink-0">
        {icon}
      </span>
      <span className="min-w-0 truncate">{children}</span>
    </>
  );
  if (!href) return <span className={chip}>{label}</span>;
  return (
    <Link
      href={href}
      className={cn(
        chip,
        "focus-visible:ring-ring hover:underline focus-visible:ring-2 focus-visible:outline-none"
      )}
    >
      {label}
      <ArrowUpRightIcon aria-hidden className="size-3 shrink-0" />
    </Link>
  );
}
