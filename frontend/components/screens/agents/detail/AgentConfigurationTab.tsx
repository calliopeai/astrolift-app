"use client";

import { useTranslations } from "next-intl";
import type * as React from "react";

import { type SettingsSectionSpec, SettingsPage } from "@/components/settings/SettingsPage";
import type { SectionSelection } from "@/components/settings/use-settings-section";
import { Section } from "@/components/ui/section";

import { AGENT_TAB_SECTIONS } from "./agent-tabs-model";

/** The Configuration sections, by the ids `?section=` and the former routes use. */
export type AgentConfigurationSection =
  | "build"
  | "run-mode"
  | "config"
  | "manifest"
  | "webhooks"
  | "model-access";

/** Each section's body, rendered by the route's containers; only the active one mounts. */
export type AgentConfigurationSlots = Partial<Record<AgentConfigurationSection, React.ReactNode>>;

export interface AgentConfigurationTabProps {
  section: SectionSelection;
  slots: AgentConfigurationSlots;
}

/**
 * The agent's Configuration tab (spec 44 §5.2, §5.3) on the settings
 * archetype in single-section mode: Build, Run mode & triggers, the config
 * editor, Manifest, CI / CD webhooks and Model access, one at a time by
 * `?section=`, so only the section on screen mounts and runs its queries
 * (Leo's page rules 1 and 2). Each part saves on its own. The frame above
 * draws the header and the tab row. Pure.
 */
export function AgentConfigurationTab({ section, slots }: AgentConfigurationTabProps) {
  const t = useTranslations("agentNavigation");
  const sections: SettingsSectionSpec[] = (AGENT_TAB_SECTIONS.configuration ?? [])
    .filter((s) => slots[s.id as AgentConfigurationSection] != null)
    .map((s) => {
      const id = s.id as AgentConfigurationSection;
      return {
        id,
        title: t(`sections.${id}`),
        content: (
          <Section
            title={t(`sections.${id}`)}
            description={t(`descriptions.${id}`)}
            divided
            className="min-w-0"
          >
            {slots[id]}
          </Section>
        ),
      };
    });
  return <SettingsPage single={section} sections={sections} />;
}
