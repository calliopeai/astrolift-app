"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";

import { cn } from "@/lib/utils";

export interface EnvironmentChoice {
  name: string;
  href: string;
  active: boolean;
}

/**
 * Which environment a tab reads (`?env=`), as links so the choice is in the
 * URL. Shown only when the layer has two or more. Pure.
 */
export function EnvironmentPicker({ environments }: { environments: EnvironmentChoice[] }) {
  const t = useTranslations("brains.overview");
  if (environments.length < 2) return null;
  return (
    <nav aria-label={t("environments")} className="flex min-w-0 flex-wrap items-center gap-1">
      <span className="text-muted-foreground mr-1 text-xs">{t("environment")}</span>
      {environments.map((env) => (
        <Link
          key={env.name}
          href={env.href}
          aria-current={env.active ? "page" : undefined}
          className={cn(
            "rounded-sm border px-2 py-0.5 font-mono text-xs transition-colors",
            env.active
              ? "bg-muted text-foreground border-border"
              : "text-muted-foreground hover:text-foreground border-transparent"
          )}
        >
          {env.name}
        </Link>
      ))}
    </nav>
  );
}
