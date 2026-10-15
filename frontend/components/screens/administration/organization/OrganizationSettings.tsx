"use client";

import { ClockIcon, InfoIcon } from "lucide-react";
import * as React from "react";
import { useTranslations } from "next-intl";

import { SettingsPage, SettingsSection } from "@/components/settings/SettingsPage";
import type { SectionSelection } from "@/components/settings/use-settings-section";
import { Badge } from "@/components/ui/badge";
import { DefinitionList } from "@/components/ui/definition-list";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Section } from "@/components/ui/section";
import { Skeleton } from "@/components/ui/skeleton";
import {
  isRestrictedSettings,
  PRODUCT_RESTRICTED_SETTINGS,
  RESTRICTED_SETTINGS,
  type RestrictedSettings,
} from "@/lib/display-prefs";
import { cn } from "@/lib/utils";

import { AdministrationShell } from "./AdministrationShell";
import type {
  OrganizationSettingsDraft,
  useOrganizationSettings,
} from "./use-organization-settings";

export type OrganizationSettingsProps = ReturnType<typeof useOrganizationSettings> & {
  /**
   * Which section is shown (`?section=`). Set by the route, so only the
   * active section is mounted and fetches (list rules 1 and 2); without
   * it every section renders, as in the catalog's overview story.
   */
  section?: SectionSelection;
  /** The house-theme section; rendered only once the org has loaded. */
  houseTheme: React.ReactNode;
  trustedDomains: React.ReactNode;
  modules: React.ReactNode;
  modelConnections?: React.ReactNode;
};

/**
 * Administration › Organization, on the settings archetype (spec 44 §5.3):
 * one section per concern, each saving on its own. There is no Danger zone:
 * the organization has no destructive setting in the app.
 */
export function OrganizationSettings({
  org,
  loading: orgsLoading,
  saving,
  onSave,
  houseTheme,
  trustedDomains,
  modules,
  modelConnections,
  section,
}: OrganizationSettingsProps) {
  const connections = useTranslations("models.shared.connections");
  const t = useTranslations("adminOrganization");
  const saved = React.useMemo(
    () => ({
      name: org?.name ?? "",
      website: org?.website ?? "",
      auditDays: String(org?.auditLogRetentionDays ?? 365),
      allowProfileEdit: org?.allowUserProfileEdit ?? true,
      allowStepUpPin: org?.allowStepUpPin ?? false,
      restrictedSettingsDefault: isRestrictedSettings(org?.restrictedSettingsDefault)
        ? org.restrictedSettingsDefault
        : PRODUCT_RESTRICTED_SETTINGS,
    }),
    [org]
  );
  const [name, setName] = React.useState(saved.name);
  const [website, setWebsite] = React.useState(saved.website);
  const [auditDays, setAuditDays] = React.useState(saved.auditDays);
  const [allowProfileEdit, setAllowProfileEdit] = React.useState(saved.allowProfileEdit);
  const [allowStepUpPin, setAllowStepUpPin] = React.useState(saved.allowStepUpPin);
  const [restrictedDefault, setRestrictedDefault] = React.useState<RestrictedSettings>(
    saved.restrictedSettingsDefault
  );

  React.useEffect(() => {
    setName(saved.name);
    setWebsite(saved.website);
    setAuditDays(saved.auditDays);
    setAllowProfileEdit(saved.allowProfileEdit);
    setAllowStepUpPin(saved.allowStepUpPin);
    setRestrictedDefault(saved.restrictedSettingsDefault);
  }, [saved]);

  // Each section sends its own edits over the saved values, so saving one
  // never carries another section's unsaved changes along with it.
  const save = (patch: Partial<OrganizationSettingsDraft>) => onSave({ ...saved, ...patch });

  const generalDirty = Boolean(org) && (name !== saved.name || website !== saved.website);
  const retentionDirty = Boolean(org) && auditDays !== saved.auditDays;
  const policiesDirty =
    Boolean(org) &&
    (allowProfileEdit !== saved.allowProfileEdit ||
      allowStepUpPin !== saved.allowStepUpPin ||
      restrictedDefault !== saved.restrictedSettingsDefault);

  const sections = [
    {
      id: "general",
      title: t("general.title"),
      content: (
        <SettingsSection
          title={t("general.title")}
          description={t("general.description")}
          dirty={generalDirty}
          saving={saving}
          onCancel={() => {
            setName(saved.name);
            setWebsite(saved.website);
          }}
          onSave={() => save({ name, website })}
        >
          {orgsLoading ? (
            <FieldSkeletons count={3} />
          ) : (
            <div className="grid min-w-0 gap-4 sm:max-w-lg">
              <div className="min-w-0 space-y-2">
                <Label htmlFor="slug">{t("general.slug")}</Label>
                <Input id="slug" value={org?.slug ?? ""} disabled className="font-mono" />
                <p className="text-muted-foreground text-xs">{t("general.slugHint")}</p>
              </div>
              <div className="min-w-0 space-y-2">
                <Label htmlFor="name">{t("general.displayName")}</Label>
                <Input id="name" value={name} onChange={(e) => setName(e.target.value)} required />
              </div>
              <div className="min-w-0 space-y-2">
                <Label htmlFor="website">{t("general.website")}</Label>
                <Input
                  id="website"
                  value={website}
                  onChange={(e) => setWebsite(e.target.value)}
                  type="url"
                  placeholder="https://acme.example"
                  className="font-mono"
                />
              </div>
            </div>
          )}
        </SettingsSection>
      ),
    },
    {
      id: "retention",
      title: t("retention.title"),
      content: (
        <SettingsSection
          title={t("retention.title")}
          description={t("retention.description")}
          dirty={retentionDirty}
          saving={saving}
          onCancel={() => setAuditDays(saved.auditDays)}
          onSave={() => save({ auditDays })}
        >
          {orgsLoading ? (
            <FieldSkeletons count={3} />
          ) : (
            <div className="grid min-w-0 gap-4 sm:grid-cols-3">
              <div className="min-w-0 space-y-2">
                <Label htmlFor="audit-days">{t("retention.auditDays")}</Label>
                <Input
                  id="audit-days"
                  type="number"
                  min={1}
                  max={2555}
                  value={auditDays}
                  onChange={(e) => setAuditDays(e.target.value)}
                  className="font-mono"
                />
                <p className="text-muted-foreground text-xs">
                  {t.rich("retention.default", {
                    value: "365",
                    mono: (chunks) => <span className="font-mono">{chunks}</span>,
                  })}
                </p>
              </div>
              <div className="min-w-0 space-y-2">
                <Label htmlFor="preview-max">{t("retention.previewCap")}</Label>
                <Input
                  id="preview-max"
                  type="number"
                  value={String(org?.previewMaxActiveDefault ?? 5)}
                  readOnly
                  disabled
                  className="font-mono"
                />
                <p className="text-muted-foreground text-xs">{t("retention.pending")}</p>
              </div>
              <div className="min-w-0 space-y-2">
                <Label htmlFor="log-days">{t("retention.appLogDays")}</Label>
                <Input
                  id="log-days"
                  type="number"
                  value={String(org?.logRetentionDaysDefault ?? 30)}
                  readOnly
                  disabled
                  className="font-mono"
                />
                <p className="text-muted-foreground text-xs">{t("retention.pending")}</p>
              </div>
            </div>
          )}
        </SettingsSection>
      ),
    },
    {
      id: "user-policies",
      title: t("policies.title"),
      content: (
        <SettingsSection
          title={t("policies.title")}
          description={t("policies.description")}
          dirty={policiesDirty}
          saving={saving}
          onCancel={() => {
            setAllowProfileEdit(saved.allowProfileEdit);
            setAllowStepUpPin(saved.allowStepUpPin);
            setRestrictedDefault(saved.restrictedSettingsDefault);
          }}
          onSave={() =>
            save({ allowProfileEdit, allowStepUpPin, restrictedSettingsDefault: restrictedDefault })
          }
        >
          {orgsLoading ? (
            <FieldSkeletons count={3} />
          ) : (
            <div className="flex min-w-0 flex-col gap-5">
              <label className="flex min-w-0 items-start gap-3 text-sm sm:max-w-xl">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={allowProfileEdit}
                  onChange={(e) => setAllowProfileEdit(e.target.checked)}
                />
                <span className="min-w-0">
                  <span className="font-medium">{t("policies.allowProfileEdit")}</span>
                  <span className="text-muted-foreground mt-0.5 block text-xs leading-relaxed">
                    {t.rich("policies.allowProfileEditHint", {
                      path: "/settings/profile",
                      code: (chunks) => <code className="font-mono">{chunks}</code>,
                    })}
                  </span>
                </span>
              </label>
              <label className="flex min-w-0 items-start gap-3 text-sm sm:max-w-xl">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={allowStepUpPin}
                  onChange={(e) => setAllowStepUpPin(e.target.checked)}
                />
                <span className="min-w-0">
                  <span className="font-medium">{t("policies.allowStepUpPin")}</span>
                  <span className="text-muted-foreground mt-0.5 block text-xs leading-relaxed">
                    {t("policies.allowStepUpPinHint")}
                  </span>
                </span>
              </label>
              <div className="min-w-0 sm:max-w-xl">
                <p id="restricted-default-label" className="text-sm font-medium">
                  {t("policies.restrictedTitle")}
                </p>
                <p className="text-muted-foreground mt-0.5 text-xs leading-relaxed">
                  {t("policies.restrictedHint")}
                </p>
                <div
                  role="radiogroup"
                  aria-labelledby="restricted-default-label"
                  className="mt-2 grid gap-2 sm:grid-cols-2"
                >
                  {(Object.keys(RESTRICTED_SETTINGS) as RestrictedSettings[]).map((key) => (
                    <button
                      key={key}
                      type="button"
                      role="radio"
                      aria-checked={restrictedDefault === key}
                      onClick={() => setRestrictedDefault(key)}
                      className={cn(
                        "border-border hover:border-primary min-w-0 rounded-md border p-3 text-left transition-colors",
                        restrictedDefault === key && "border-primary ring-primary ring-1"
                      )}
                    >
                      <span className="block text-sm font-semibold">
                        {t(`policies.restricted.${key}.label`)}
                      </span>
                      <span className="text-muted-foreground block text-xs">
                        {t(`policies.restricted.${key}.blurb`)}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}
        </SettingsSection>
      ),
    },
    ...(org && houseTheme
      ? [{ id: "house-theme", title: t("sections.houseTheme"), content: houseTheme }]
      : []),
    { id: "trusted-domains", title: t("sections.trustedDomains"), content: trustedDomains },
    { id: "modules", title: t("sections.modules"), content: modules },
    ...(modelConnections
      ? [{ id: "model-connections", title: connections("policyTitle"), content: modelConnections }]
      : []),
    {
      id: "identity-provider",
      title: t("idp.title"),
      content: <IdentityProviderSection />,
    },
  ];

  return (
    <AdministrationShell fnKey="organization" title={t("title")} description={t("description")}>
      <SettingsPage sections={sections} single={section} />
    </AdministrationShell>
  );
}

function FieldSkeletons({ count }: { count: number }) {
  return (
    <div className="flex flex-col gap-3" aria-busy="true">
      {Array.from({ length: count }).map((_, i) => (
        <Skeleton key={i} className="h-10 w-full max-w-md" />
      ))}
    </div>
  );
}

function IdentityProviderSection() {
  const t = useTranslations("adminOrganization.idp");
  return (
    <Section
      title={t("title")}
      description={t("description")}
      divided
      action={
        <Badge variant="outline" className="gap-1">
          <ClockIcon className="size-3" />
          {t("comingSoon")}
        </Badge>
      }
    >
      <div className="grid min-w-0 gap-4">
        <div className="min-w-0 space-y-2">
          <p className="text-muted-foreground text-xs">{t("activeByEnvironment")}</p>
          <DefinitionList
            items={[
              { term: t("development"), description: t("devBypass") },
              { term: t("stagingProduction"), description: "Auth0" },
            ]}
          />
        </div>
        <div className="border-info-border bg-info/5 flex min-w-0 items-start gap-2 rounded-md border p-3">
          <InfoIcon className="text-info-fg mt-0.5 size-3.5 shrink-0" />
          <p className="text-muted-foreground min-w-0 text-xs leading-relaxed">{t("roadmap")}</p>
        </div>
      </div>
    </Section>
  );
}
