"use client";

import { ClockIcon, InfoIcon } from "lucide-react";
import * as React from "react";

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
  section,
}: OrganizationSettingsProps) {
  const saved = React.useMemo(
    () => ({
      name: org?.name ?? "",
      website: org?.website ?? "",
      auditDays: String(org?.auditLogRetentionDays ?? 365),
      allowProfileEdit: org?.allowUserProfileEdit ?? true,
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
  const [restrictedDefault, setRestrictedDefault] = React.useState<RestrictedSettings>(
    saved.restrictedSettingsDefault
  );

  React.useEffect(() => {
    setName(saved.name);
    setWebsite(saved.website);
    setAuditDays(saved.auditDays);
    setAllowProfileEdit(saved.allowProfileEdit);
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
      restrictedDefault !== saved.restrictedSettingsDefault);

  const sections = [
    {
      id: "general",
      title: "General",
      content: (
        <SettingsSection
          title="General"
          description="Display name and homepage URL surfaced in the sidebar and emails."
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
                <Label htmlFor="slug">Slug</Label>
                <Input id="slug" value={org?.slug ?? ""} disabled className="font-mono" />
                <p className="text-muted-foreground text-xs">
                  Slug is immutable — used in URLs and namespace prefixes.
                </p>
              </div>
              <div className="min-w-0 space-y-2">
                <Label htmlFor="name">Display name</Label>
                <Input id="name" value={name} onChange={(e) => setName(e.target.value)} required />
              </div>
              <div className="min-w-0 space-y-2">
                <Label htmlFor="website">Website</Label>
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
      title: "Retention defaults",
      content: (
        <SettingsSection
          title="Retention defaults"
          description="Defaults inherited by new apps. Existing apps retain their own values until edited explicitly."
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
                <Label htmlFor="audit-days">Audit log days</Label>
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
                  Default <span className="font-mono">365</span>.
                </p>
              </div>
              <div className="min-w-0 space-y-2">
                <Label htmlFor="preview-max">Preview env cap</Label>
                <Input
                  id="preview-max"
                  type="number"
                  value={String(org?.previewMaxActiveDefault ?? 5)}
                  readOnly
                  disabled
                  className="font-mono"
                />
                <p className="text-muted-foreground text-xs">
                  Editable once the App registry surface lands.
                </p>
              </div>
              <div className="min-w-0 space-y-2">
                <Label htmlFor="log-days">App log days</Label>
                <Input
                  id="log-days"
                  type="number"
                  value={String(org?.logRetentionDaysDefault ?? 30)}
                  readOnly
                  disabled
                  className="font-mono"
                />
                <p className="text-muted-foreground text-xs">
                  Editable once the App registry surface lands.
                </p>
              </div>
            </div>
          )}
        </SettingsSection>
      ),
    },
    {
      id: "user-policies",
      title: "User policies",
      content: (
        <SettingsSection
          title="User policies"
          description="Org-wide rules that apply to every member, including the self-service profile editor."
          dirty={policiesDirty}
          saving={saving}
          onCancel={() => {
            setAllowProfileEdit(saved.allowProfileEdit);
            setRestrictedDefault(saved.restrictedSettingsDefault);
          }}
          onSave={() => save({ allowProfileEdit, restrictedSettingsDefault: restrictedDefault })}
        >
          {orgsLoading ? (
            <FieldSkeletons count={2} />
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
                  <span className="font-medium">Allow users to edit their own profile</span>
                  <span className="text-muted-foreground mt-0.5 block text-xs leading-relaxed">
                    Lets users update their first name, last name, and email on{" "}
                    <code className="font-mono">/settings/profile</code>. Fields managed by the
                    identity provider stay locked even when this is on — local edits to IdP-claimed
                    fields would be overwritten on next sign-in. Turn this off for SSO-only
                    deployments where the IdP is the source of truth.
                  </span>
                </span>
              </label>
              <div className="min-w-0 sm:max-w-xl">
                <p id="restricted-default-label" className="text-sm font-medium">
                  Settings members can&apos;t change
                </p>
                <p className="text-muted-foreground mt-0.5 text-xs leading-relaxed">
                  The default for members who haven&apos;t chosen under Settings › Appearance.
                  Anyone who picks their own keeps it.
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
                        {RESTRICTED_SETTINGS[key].label}
                      </span>
                      <span className="text-muted-foreground block text-xs">
                        {RESTRICTED_SETTINGS[key].blurb}
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
      ? [{ id: "house-theme", title: "House theme", content: houseTheme }]
      : []),
    { id: "trusted-domains", title: "Trusted domains", content: trustedDomains },
    { id: "modules", title: "Modules", content: modules },
    {
      id: "identity-provider",
      title: "Identity provider",
      content: <IdentityProviderSection />,
    },
  ];

  return (
    <AdministrationShell
      fnKey="organization"
      title="Organization"
      description="Edit the platform's organization-level identity and retention defaults."
    >
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
  return (
    <Section
      title="Identity provider"
      description="OIDC, SAML, and SCIM sign-in configuration for this organization."
      divided
      action={
        <Badge variant="outline" className="gap-1">
          <ClockIcon className="size-3" />
          In-app setup coming soon
        </Badge>
      }
    >
      <div className="grid min-w-0 gap-4">
        <div className="min-w-0 space-y-2">
          <p className="text-muted-foreground text-xs">Currently active, by environment:</p>
          <DefinitionList
            items={[
              { term: "Development", description: "auth1 dev-login bypass" },
              { term: "Staging & production", description: "Auth0" },
            ]}
          />
        </div>
        <div className="border-info-border bg-info/5 flex min-w-0 items-start gap-2 rounded-md border p-3">
          <InfoIcon className="text-info-fg mt-0.5 size-3.5 shrink-0" />
          <p className="text-muted-foreground min-w-0 text-xs leading-relaxed">
            A dedicated setup sub-route will add OIDC discovery, SAML metadata, and SCIM token
            rotation once the in-app provider picker is wired.
          </p>
        </div>
      </div>
    </Section>
  );
}
