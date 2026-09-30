"use client";

import { useTranslations } from "next-intl";
import type * as React from "react";

import { Restricted, useRestrictedMode } from "@/components/settings/Restricted";
import { type SettingsSectionSpec, SettingsPage } from "@/components/settings/SettingsPage";
import type { SectionSelection } from "@/components/settings/use-settings-section";
import { Section } from "@/components/ui/section";
import type { RestrictedSettings } from "@/lib/display-prefs";

/** What the viewer may change in each section; optimistic while permissions load. */
export interface AppAccessAccess {
  /** `org.manage_members` */
  members: boolean;
  /** `app.deploy` */
  tokens: boolean;
  /** `app.update` (the supply-chain policy) */
  security: boolean;
  /** `app.access` (the edge rule) */
  edge: boolean;
}

/** Each section's body, rendered by the route's containers. */
export interface AppAccessSlots {
  members?: React.ReactNode;
  tokens?: React.ReactNode;
  security?: React.ReactNode;
  edge?: React.ReactNode;
}

export interface AppAccessScreenProps {
  section: SectionSelection;
  access: AppAccessAccess;
  slots: AppAccessSlots;
  /** Overrides the person's "settings you can't change" preference (stories). */
  restrictedMode?: RestrictedSettings;
}

const SECTIONS: {
  id: keyof AppAccessSlots;
  permission: string;
  descriptionKey?: "membersDescription" | "edgeDescription";
}[] = [
  {
    id: "members",
    permission: "org.manage_members",
    descriptionKey: "membersDescription",
  },
  { id: "tokens", permission: "app.deploy" },
  { id: "security", permission: "app.update" },
  {
    id: "edge",
    permission: "app.access",
    descriptionKey: "edgeDescription",
  },
];

/**
 * The app's Access tab (spec 44 §5.2, §10.3; access design 3.3): who may
 * reach and change the app, on the settings archetype in single-section
 * mode. People with access (roles and team shares), Deploy tokens, Security
 * scans and Edge access (the central-auth rule in front of the app, with how
 * it combines with roles said once), one at a time by `?section=`. A section the viewer may not
 * change shows disabled with the permission that would allow it, or, when
 * the person hides what they can't change, leaves the nav. Only the active
 * section is mounted, so only its query runs. Pure.
 */
export function AppAccessScreen({ section, access, slots, restrictedMode }: AppAccessScreenProps) {
  const t = useTranslations("apps.frame.sections");
  const tAccess = useTranslations("apps.security.access");
  const tTab = useTranslations("apps.settingsTab");
  const hide = useRestrictedMode(restrictedMode) === "hide";

  const sections: SettingsSectionSpec[] = SECTIONS.filter(
    (s) => slots[s.id] !== undefined && !(hide && !access[s.id])
  ).map((s) => ({
    id: s.id,
    title: t(s.id),
    content: (
      <Section
        title={t(s.id)}
        description={s.descriptionKey ? tAccess(s.descriptionKey) : undefined}
        divided
        className="min-w-0"
      >
        <Restricted mode={restrictedMode} allowed={access[s.id]} permission={s.permission}>
          {slots[s.id]}
        </Restricted>
      </Section>
    ),
  }));

  if (sections.length === 0) {
    return <p className="text-muted-foreground text-sm">{tTab("empty")}</p>;
  }
  return <SettingsPage single={section} sections={sections} />;
}
