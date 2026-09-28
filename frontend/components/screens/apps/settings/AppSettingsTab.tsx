"use client";

import { AlertTriangleIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import type * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { Restricted, useRestrictedMode } from "@/components/settings/Restricted";
import { type SettingsSectionSpec, SettingsPage } from "@/components/settings/SettingsPage";
import type { SectionSelection } from "@/components/settings/use-settings-section";
import { Section } from "@/components/ui/section";
import { Skeleton } from "@/components/ui/skeleton";
import type { RestrictedSettings } from "@/lib/display-prefs";

/**
 * The parts of the Settings tab that carry data of their own, rendered by
 * the route's containers. Single-section mode mounts only the active
 * section, so a slot's hook runs only while its section is on screen.
 */
export interface AppSettingsTabSlots {
  /** Grace-period cancel banner (#436 B), first in General. */
  deregisterPending?: React.ReactNode;
  /** Name, description, repository (AppIdentityView). */
  identity?: React.ReactNode;
  assignProject?: React.ReactNode;
  teams?: React.ReactNode;
  deployStrategy?: React.ReactNode;
  /** Shown only when the app has a repository. */
  ciSetup?: React.ReactNode;
  resync?: React.ReactNode;
  controls?: React.ReactNode;
  webhookDeploys?: React.ReactNode;
  forceRedeploy?: React.ReactNode;
  runJob?: React.ReactNode;
  ingress?: React.ReactNode;
  retention?: React.ReactNode;
  managedServicesSummary?: React.ReactNode;
  managedServicesAdmin?: React.ReactNode;
  /** The config editor (with the agent-config builder for an agent). */
  configuration?: React.ReactNode;
  manifest?: React.ReactNode;
  webhooks?: React.ReactNode;
  environments?: React.ReactNode;
  environmentSettings?: React.ReactNode;
  /** Danger zone rows. */
  archive?: React.ReactNode;
  deregister?: React.ReactNode;
}

/** What the viewer may change; optimistic while permissions load, as `Can` is. */
export interface AppSettingsTabAccess {
  /** `app.update` */
  update: boolean;
  /** `app.deploy` */
  deploy: boolean;
  /** `app.delete` */
  delete: boolean;
  /** `webhook.update` */
  webhooks: boolean;
}

export interface AppSettingsTabProps {
  slug: string;
  /** First load of the app. */
  loading: boolean;
  /** The app was found (the frame shows not-found itself; this is the fallback). */
  found: boolean;
  section: SectionSelection;
  access: AppSettingsTabAccess;
  slots: AppSettingsTabSlots;
  /** Overrides the person's "settings you can't change" preference (stories). */
  restrictedMode?: RestrictedSettings;
}

type SlotKey = keyof AppSettingsTabSlots;
type AccessKey = keyof AppSettingsTabAccess;

/** General, grouped by concern. Each part inside saves or acts on its own. */
const GENERAL_GROUPS: {
  key: string;
  permission: string;
  access: AccessKey;
  slots: SlotKey[];
}[] = [
  {
    key: "app",
    permission: "app.update",
    access: "update",
    slots: ["identity", "assignProject", "teams"],
  },
  {
    key: "source",
    permission: "app.update",
    access: "update",
    slots: ["deployStrategy", "ciSetup", "resync"],
  },
  {
    key: "deploys",
    permission: "app.deploy",
    access: "deploy",
    slots: ["controls", "webhookDeploys", "forceRedeploy", "runJob"],
  },
  { key: "traffic", permission: "app.deploy", access: "deploy", slots: ["ingress"] },
  { key: "data", permission: "app.update", access: "update", slots: ["retention"] },
  {
    key: "managedServices",
    permission: "app.deploy",
    access: "deploy",
    slots: ["managedServicesSummary", "managedServicesAdmin"],
  },
];

const present = (node: React.ReactNode) => node !== undefined && node !== null && node !== false;

/**
 * The app's Settings tab on the settings archetype (spec 44 §5.2, §5.3), in
 * single-section mode: General (grouped by concern), Configuration,
 * Manifest, Webhooks, Environments and one Danger zone, chosen by
 * `?section=`. A part the viewer may not change shows disabled with the
 * permission that would allow it, or, when the person hides what they
 * can't change, is left out, and a section left empty leaves the nav. The
 * frame above draws the header and the tab row; this draws neither. Pure.
 */
export function AppSettingsTab({
  slug,
  loading,
  found,
  section,
  access,
  slots,
  restrictedMode,
}: AppSettingsTabProps) {
  const t = useTranslations("apps.settingsTab");
  const tSection = useTranslations("apps.frame.sections");
  const tCommon = useTranslations("apps.common");
  const hide = useRestrictedMode(restrictedMode) === "hide";

  if (loading) {
    return (
      <div className="flex min-w-0 flex-col gap-4" aria-busy>
        <Skeleton className="h-9 w-48" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  if (!found) {
    return (
      <EmptyState
        icon={<AlertTriangleIcon className="size-5" />}
        title={tCommon("notFoundSlug", { slug })}
        description={tCommon("notFoundDescription")}
        actionHref="/apps"
        actionLabel={tCommon("backToApps")}
      />
    );
  }

  const gate = (
    allowed: boolean,
    permission: string,
    node: React.ReactNode
  ): React.ReactNode | null =>
    !present(node) || (hide && !allowed) ? null : (
      <Restricted mode={restrictedMode} allowed={allowed} permission={permission}>
        {node}
      </Restricted>
    );

  const groups = GENERAL_GROUPS.map((g) => {
    const parts = g.slots.filter((k) => present(slots[k]));
    const body = parts.length ? (
      <div className="flex min-w-0 flex-col gap-8">
        {parts.map((k) => (
          <div key={k} className="min-w-0">
            {slots[k]}
          </div>
        ))}
      </div>
    ) : null;
    const gated = gate(access[g.access], g.permission, body);
    return gated ? (
      <div key={g.key} className="flex min-w-0 flex-col gap-4">
        <h3 className="text-muted-foreground text-2xs font-mono tracking-wider uppercase">
          {t(`groups.${g.key}`)}
        </h3>
        {gated}
      </div>
    ) : null;
  }).filter(Boolean);

  const titled = (id: string, label: string, body: React.ReactNode): SettingsSectionSpec => ({
    id,
    title: label,
    content: (
      <Section title={label} divided className="min-w-0">
        {body}
      </Section>
    ),
  });

  const sections: SettingsSectionSpec[] = [];
  if (present(slots.deregisterPending) || groups.length) {
    sections.push(
      titled(
        "general",
        tSection("general"),
        <div className="flex min-w-0 flex-col gap-10">
          {slots.deregisterPending}
          {groups}
        </div>
      )
    );
  }
  const configuration = gate(access.update, "app.update", slots.configuration);
  if (configuration)
    sections.push(titled("configuration", tSection("configuration"), configuration));
  if (present(slots.manifest))
    sections.push(titled("manifest", tSection("manifest"), slots.manifest));
  const webhooks = gate(access.webhooks, "webhook.update", slots.webhooks);
  if (webhooks) sections.push(titled("webhooks", tSection("webhooks"), webhooks));
  const overrides = gate(access.update, "app.update", slots.environmentSettings);
  if (present(slots.environments) || overrides) {
    sections.push(
      titled(
        "environments",
        tSection("environments"),
        <div className="flex min-w-0 flex-col gap-10">
          {slots.environments}
          {overrides}
        </div>
      )
    );
  }

  const archive = gate(access.update, "app.update", slots.archive);
  const deregister = gate(access.delete, "app.delete", slots.deregister);
  const dangerZone =
    archive || deregister ? (
      <>
        {archive}
        {deregister}
      </>
    ) : undefined;

  if (sections.length === 0 && !dangerZone) {
    return <p className="text-muted-foreground text-sm">{t("empty")}</p>;
  }

  return <SettingsPage single={section} sections={sections} dangerZone={dangerZone} />;
}
