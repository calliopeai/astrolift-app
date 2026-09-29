"use client";

import type * as React from "react";

import { DetailTabSections } from "@/components/detail/DetailTabSections";

import { AGENT_TAB_SECTIONS, type AgentTabKey, agentSectionHref } from "./agent-tabs-model";

export interface AgentTabSectionsProps {
  slug: string;
  /** A tab that holds sections (Configuration, Skills & tools, Logs & metrics, Access). */
  tab: AgentTabKey;
  /** The section on screen, from `activeAgentSection`. */
  active: string;
  /** The active section's body; the route mounts only that one. */
  children: React.ReactNode;
}

/**
 * The sections inside a consolidated agent tab (spec 44 §5.2), on the
 * shared DetailTabSections: each is `?section=` in the URL. Settings uses
 * SettingsPage's own section nav instead. Pure.
 */
export function AgentTabSections({ slug, tab, active, children }: AgentTabSectionsProps) {
  const sections = (AGENT_TAB_SECTIONS[tab] ?? []).map((s) => ({
    id: s.id,
    label: s.label,
    href: agentSectionHref(slug, tab, s.id),
  }));
  return (
    <DetailTabSections ariaLabel="Sections" sections={sections} active={active}>
      {children}
    </DetailTabSections>
  );
}
