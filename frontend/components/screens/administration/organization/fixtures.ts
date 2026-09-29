import type {
  AstroliftOrganization,
  AstroliftOrganizationAllowlistedDomain,
  AstroliftRole,
} from "@/graphql/identity/identity.types";

import type { HouseThemeCardProps } from "./HouseThemeCard";
import type { ModulesCardProps } from "./ModulesCard";
import type { OrganizationSettingsProps } from "./OrganizationSettings";
import type { TrustedDomainsCardProps } from "./TrustedDomainsCard";
import type { ModuleItem } from "./use-modules-card";

const LONG =
  "Intergalactic Heavy Industries Consolidated Holdings and Subsidiary Launch Operations Worldwide";

/** The long-string fixtures every screen is checked against (spec 44 §8). */
export const LONG_SHA = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08";
export const LONG_ARN =
  "arn:aws:iam::123456789012:role/astrolift/organizations/intergalactic-heavy-industries/" +
  "identity/trusted-domains/sso-auto-join/permission-boundaries/astrolift-organization-admin-boundary-v2-us-east-2a-1";
export const LONG_URL =
  "https://launch-operations.intergalactic-heavy-industries-consolidated-holdings.example/" +
  "organizations/intergalactic-heavy-industries/settings?ref=9f86d081884c7d659a2feaa0c55ad015";

export const ORG: AstroliftOrganization = {
  id: "org-1",
  slug: "acme",
  name: "Acme Launch Co.",
  website: "https://acme.example",
  allowUserProfileEdit: true,
  appearanceDefault: { ground: "emerald", accent: "green" },
  appearanceLocked: false,
  restrictedSettingsDefault: "show",
  auditLogRetentionDays: 365,
  previewMaxActiveDefault: 5,
  logRetentionDaysDefault: 30,
  metricsRetentionDaysDefault: 15,
  metricsRollupRetentionDaysDefault: 395,
  traceRetentionDaysDefault: 7,
  defaultResourceTags: {},
  managedServiceIsolationPolicy: {},
  scimEnabled: false,
  deletedAt: null,
  onboardingCompletedAt: "2026-08-01T12:00:00Z",
  createdAt: "2026-01-15T12:00:00Z",
  updatedAt: "2026-09-20T12:00:00Z",
};

export const ORG_UNTHEMED: AstroliftOrganization = {
  ...ORG,
  appearanceDefault: {},
  appearanceLocked: false,
};

export const ORG_LOCKED: AstroliftOrganization = {
  ...ORG,
  appearanceDefault: { ground: "paper", accent: "copper" },
  appearanceLocked: true,
};

/** Members who haven't chosen don't see settings they can't change. */
export const ORG_HIDES_RESTRICTED: AstroliftOrganization = {
  ...ORG,
  restrictedSettingsDefault: "hide",
};

export const ORG_LONG: AstroliftOrganization = {
  ...ORG,
  slug: "intergalactic-heavy-industries-consolidated-holdings-launch-ops",
  name: LONG,
  website: LONG_URL,
};

const ok = async () => true;
const fail = async () => false;

export const orgSettings: Omit<
  OrganizationSettingsProps,
  "houseTheme" | "trustedDomains" | "modules"
> = { org: ORG, loading: false, saving: false, onSave: ok };

export const orgSettingsLoading: typeof orgSettings = {
  org: null,
  loading: true,
  saving: false,
  onSave: ok,
};

export const orgSettingsNoOrg: typeof orgSettings = {
  org: null,
  loading: false,
  saving: false,
  onSave: fail,
};

export const orgSettingsSaveFails: typeof orgSettings = { ...orgSettings, onSave: fail };

export const orgSettingsLong: typeof orgSettings = { ...orgSettings, org: ORG_LONG };

export const houseTheme: HouseThemeCardProps = { org: ORG, saving: false, onSave: ok };

export const houseThemeUnset: HouseThemeCardProps = { ...houseTheme, org: ORG_UNTHEMED };

export const houseThemeLocked: HouseThemeCardProps = { ...houseTheme, org: ORG_LOCKED };

export const houseThemeSaving: HouseThemeCardProps = { ...houseTheme, saving: true };

export const houseThemeSaveFails: HouseThemeCardProps = { ...houseTheme, onSave: fail };

const ROLES: AstroliftRole[] = [
  {
    id: "role-1",
    slug: "org_admin",
    name: "Organization admin",
    description: "Full control of the organization.",
    isSystem: true,
    permissions: [],
    scopeLevel: "ORG",
  },
  {
    id: "role-2",
    slug: "team_viewer",
    name: "Team viewer",
    description: "Read-only access to a team.",
    isSystem: true,
    permissions: [],
    scopeLevel: "TEAM",
  },
];

const domain = (
  id: string,
  name: string,
  defaultRoleSlug: string | null,
  requiresReview: boolean
): AstroliftOrganizationAllowlistedDomain => ({
  id,
  domain: name,
  defaultRoleSlug,
  requiresReview,
  createdAt: "2026-09-01T12:00:00Z",
  updatedAt: "2026-09-01T12:00:00Z",
});

/** The card's props less the list controller, which the story builds. */
export type TrustedDomainsFixture = Omit<TrustedDomainsCardProps, "list">;

export const trustedDomains: TrustedDomainsFixture = {
  rows: [
    domain("d1", "acme.example", "team_viewer", false),
    domain("d2", "contractors.acme.example", null, true),
  ],
  totalCount: 2,
  loading: false,
  error: null,
  onRetry: () => {},
  roleOptions: ROLES,
  adding: false,
  removing: false,
  onAdd: ok,
  onRemove: async () => {},
};

export const trustedDomainsLoading: TrustedDomainsFixture = {
  ...trustedDomains,
  rows: [],
  loading: true,
};

export const trustedDomainsEmpty: TrustedDomainsFixture = {
  ...trustedDomains,
  rows: [],
  totalCount: 0,
};

export const trustedDomainsRemoveFails: TrustedDomainsFixture = {
  ...trustedDomains,
  onAdd: fail,
  onRemove: async () => {
    throw new Error("Remove failed");
  },
};

export const trustedDomainsLong: TrustedDomainsFixture = {
  ...trustedDomains,
  totalCount: 1,
  rows: [
    domain(
      "d3",
      "engineering.launch-operations.intergalactic-heavy-industries-consolidated.example",
      "intergalactic_launch_operations_extended_read_only_viewer",
      true
    ),
  ],
};

const MODULE_ITEMS: ModuleItem[] = [
  {
    key: "chat_studio_integration",
    label: "Chat Studio integration",
    description: "Lets Chat Studio reach this organization's apps through the builder API.",
    enabled: true,
    installAllowed: true,
  },
  {
    key: "agent_live_attach",
    label: "Agent live attach",
    description: "Lets members attach live to a running agent session.",
    enabled: false,
    installAllowed: false,
  },
  {
    key: "chat_studio_agent_runs",
    label: "Chat Studio agent runs",
    description: "Lets Chat Studio launch this organization's registered agents.",
    enabled: false,
    installAllowed: true,
  },
];

export const modules: ModulesCardProps = {
  items: MODULE_ITEMS,
  loading: false,
  canManage: true,
  permsLoading: false,
  pendingKey: null,
  onToggle: async () => {},
};

export const modulesLoading: ModulesCardProps = { ...modules, loading: true };

export const modulesReadOnly: ModulesCardProps = { ...modules, canManage: false };

export const modulesAllOff: ModulesCardProps = {
  ...modules,
  items: MODULE_ITEMS.map((m) => ({ ...m, enabled: false, installAllowed: false })),
};

export const modulesLong: ModulesCardProps = {
  ...modules,
  items: MODULE_ITEMS.map((m) => ({
    ...m,
    label: `${m.label} for ${LONG}`,
    description: `${m.description} ${LONG}, including every subsidiary launch site.`,
  })),
};
