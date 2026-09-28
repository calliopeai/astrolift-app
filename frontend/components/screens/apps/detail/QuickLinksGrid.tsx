"use client";

import {
  BoxesIcon,
  ChartLineIcon,
  ChevronRightIcon,
  GlobeIcon,
  RocketIcon,
  SettingsIcon,
  ShieldIcon,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { Panel, type PanelSpan } from "@/components/panel/Panel";

import type { useQuickLinks } from "./use-quick-links";

export type QuickLinksGridProps = ReturnType<typeof useQuickLinks> & {
  /** The app's own base path, e.g. `/apps/acme` (or `/agents/acme`). */
  appHref: string;
  span?: PanelSpan;
};

const LINKS: { key: string; segment: string; icon: LucideIcon }[] = [
  { key: "deployments", segment: "deployments", icon: RocketIcon },
  { key: "workloads", segment: "workloads", icon: BoxesIcon },
  { key: "logs", segment: "logs", icon: ChartLineIcon },
  { key: "domains", segment: "domains", icon: GlobeIcon },
  { key: "access", segment: "access", icon: ShieldIcon },
  { key: "settings", segment: "settings", icon: SettingsIcon },
];

/**
 * The overview's quick links (#408): six places, a name and a sentence each.
 * Six links with a sentence are a list, not cards (astrolift-design §11), so
 * this is one panel of hairline rows. Deployments carries the live count.
 */
export function QuickLinksGrid({ appHref, count, loading, span = 4 }: QuickLinksGridProps) {
  const t = useTranslations("apps.overview.links");
  return (
    <Panel title={t("title")} span={span} flush>
      <ul className="divide-y">
        {LINKS.map(({ key, segment, icon: Icon }) => (
          <li key={key}>
            <Link
              href={`${appHref}/${segment}`}
              className="group hover:bg-muted/40 focus-visible:ring-ring/50 flex min-w-0 items-start gap-3 px-4 py-2.5 outline-none focus-visible:ring-3"
            >
              <Icon aria-hidden className="text-muted-foreground mt-0.5 size-4 shrink-0" />
              <span className="min-w-0 flex-1">
                <span className="flex min-w-0 items-baseline gap-2">
                  <span className="text-sm font-medium">{t(`${key}.title`)}</span>
                  {key === "deployments" && !(loading && count === 0) && (
                    <span className="text-muted-foreground text-2xs font-mono">
                      {t("count", { count })}
                    </span>
                  )}
                </span>
                <span className="text-muted-foreground block text-xs">
                  {t(`${key}.description`)}
                </span>
              </span>
              <ChevronRightIcon
                aria-hidden
                className="text-muted-foreground mt-0.5 size-4 shrink-0 opacity-0 transition-opacity group-hover:opacity-100"
              />
            </Link>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
