"use client";

import { ChevronRightIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { PageShell } from "@/components/PageShell";
import { Card, CardContent } from "@/components/ui/card";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { formatRelativeAge } from "@/lib/format";

import { SETTINGS_NAV_FLAT, type SettingsNavItem, type SettingsSectionKey } from "./settings-nav";

/**
 * Org-scoped surfaces report staleness off the parent organization's
 * `updatedAt` — same heuristic the per-app settings landing uses for
 * its links (#437 scope E). Personal surfaces (Profile / Security /
 * Notifications) and the cross-link out to /tokens do not carry an
 * org-wide proxy, so they render without a caption rather than
 * showing a misleading "Modified" timestamp.
 *
 * A from:backend ticket tracks adding per-surface `lastModifiedAt`
 * fields so we can drop this mapping and read straight from the
 * source.
 */
const ORG_SCOPED: ReadonlySet<SettingsSectionKey> = new Set([
  "organization",
  "identityProvider",
  "sourceProviders",
  "policies",
  "permissions",
]);

export function SettingsLandingClient() {
  const t = useTranslations("settingsIndex");
  const { org } = useActiveOrg();
  const orgUpdatedAt = org?.updatedAt ?? null;

  return (
    <PageShell title={t("title")} description={t("description")}>
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {SETTINGS_NAV_FLAT.map((section) => (
          <SettingsCard
            key={section.key}
            section={section}
            lastModifiedAt={ORG_SCOPED.has(section.key) ? orgUpdatedAt : null}
          />
        ))}
      </div>
    </PageShell>
  );
}

function SettingsCard({
  section,
  lastModifiedAt,
}: {
  section: SettingsNavItem;
  lastModifiedAt: string | null;
}) {
  const t = useTranslations("settingsIndex.sections");
  const tMeta = useTranslations("settingsIndex.cardMeta");
  const Icon = section.icon;
  return (
    <Link href={section.href} className="group block" aria-label={t(`${section.i18nKey}.title`)}>
      <Card className="hover:bg-accent/40 h-full transition-all group-hover:-translate-y-0.5 group-hover:shadow-sm">
        <CardContent className="flex items-start gap-4 p-5">
          <div className="bg-primary/10 text-primary shrink-0 rounded-md p-2.5">
            <Icon className="size-5" />
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold">{t(`${section.i18nKey}.title`)}</p>
            <p className="text-muted-foreground mt-0.5 text-xs">
              {t(`${section.i18nKey}.description`)}
            </p>
            {lastModifiedAt && (
              <p className="text-muted-foreground mt-1 text-xs">
                {tMeta("modified", { when: formatRelativeAge(lastModifiedAt) })}
              </p>
            )}
          </div>
          <ChevronRightIcon className="text-muted-foreground group-hover:text-foreground size-5 shrink-0 transition-colors" />
        </CardContent>
      </Card>
    </Link>
  );
}
