import { NextIntlClientProvider } from "next-intl";
import en from "@/messages/en.json";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { DnsConnectionWizard, type DnsConnectionWizardProps } from "./DnsConnectionWizard";
import { DOMAIN_ACTIVE } from "./domains-environments.fixtures";

const connection = {
  id: "d0000000-0000-4000-8000-000000000001",
  version: 3,
  name: "Public DNS",
  provider: "CLOUDFLARE",
  authMethod: "API_TOKEN",
  state: "ACTIVE",
  revocationState: "NOT_REQUESTED",
  verifiedAt: "2026-10-04T01:00:00Z",
  expiresAt: null,
  dnsWritesSupported: false,
};
const zone = {
  id: "0123456789abcdef0123456789abcdef",
  name: "acme.example",
  accountId: "fedcba9876543210fedcba9876543210",
  status: "active",
  nameServers: ["ada.ns.cloudflare.com", "ben.ns.cloudflare.com"],
};
const noop = () => {},
  yes = async () => true;
export const DNS_SETUP: DnsConnectionWizardProps = {
  provider: null,
  onProvider: noop,
  support: {
    allowed: true,
    reason: "",
    apiTokenSupported: true,
    oauthConfigured: false,
    oauthSetupReason: "OAUTH_CLIENT_NOT_CONFIGURED",
    dnsWritesSupported: false,
  },
  supportLoading: false,
  supportError: null,
  allowed: true,
  connections: { page: 1, pageSize: 20, totalCount: 1, nextCursor: null, items: [connection] },
  connectionsLoading: false,
  connectionsError: null,
  page: 1,
  onPage: noop,
  connection,
  onConnection: noop,
  zones: { complete: true, reason: "", items: [zone] },
  zonesLoading: false,
  zonesError: null,
  selectedZone: zone,
  onZone: noop,
  records: {
    complete: true,
    reason: "",
    items: [
      {
        id: "11111111111111111111111111111111",
        name: "www.acme.example",
        type: "CNAME",
        content: "origin.acme.example",
        ttl: 300,
        proxied: true,
        priority: null,
      },
    ],
  },
  recordsLoading: false,
  recordsError: null,
  target: null,
  targetLoading: false,
  targetError: null,
  route53Domains: [DOMAIN_ACTIVE],
  route53Loading: false,
  route53Error: null,
  binding: null,
  currentBinding: null,
  verification: null,
  bindingLoading: false,
  bindingError: null,
  busy: false,
  message: null,
  actionError: null,
  uncertain: false,
  onConnect: yes,
  onOAuth: yes,
  onRetest: yes,
  onDisconnect: yes,
  onRegister: yes,
  onVerify: yes,
  canVerify: false,
  onRefresh: noop,
};
const meta: Meta<typeof DnsConnectionWizard> = {
  title: "Screens/Domains/DnsConnectionWizard",
  component: DnsConnectionWizard,
  parameters: { layout: "fullscreen" },
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <Story />
      </NextIntlClientProvider>
    ),
  ],
  args: DNS_SETUP,
};
export default meta;
type Story = StoryObj<typeof meta>;
export const Providers: Story = {};
export const UncertainProviders: Story = {
  args: {
    uncertain: true,
    actionError: "Read current connections and domain bindings before another write.",
  },
};
export const Loading: Story = { args: { support: null, supportLoading: true, allowed: false } };
export const Denied: Story = {
  args: {
    support: { ...DNS_SETUP.support!, allowed: false, reason: "PLATFORM_OPERATOR_REQUIRED" },
    allowed: false,
  },
};
export const SupportUnavailable: Story = {
  args: {
    support: null,
    supportError: "Current provider connection admission could not be read.",
    allowed: false,
  },
};
export const Connections: Story = { args: { provider: "cloudflare", initialStep: "connection" } };
export const OAuthAvailable: Story = {
  args: {
    provider: "cloudflare",
    initialStep: "connection",
    support: { ...DNS_SETUP.support!, oauthConfigured: true, oauthSetupReason: "" },
  },
};
export const EmptyConnections: Story = {
  args: {
    provider: "cloudflare",
    initialStep: "connection",
    connections: { ...DNS_SETUP.connections!, totalCount: 0, items: [] },
  },
};
export const ConnectionReadFailed: Story = {
  args: {
    provider: "cloudflare",
    initialStep: "connection",
    connections: null,
    connectionsError: "Connection metadata could not be read.",
  },
};
export const Zones: Story = { args: { provider: "cloudflare", initialStep: "zone" } };
export const PartialZones: Story = {
  args: {
    provider: "cloudflare",
    initialStep: "zone",
    zones: { ...DNS_SETUP.zones!, complete: false, reason: "INCOMPLETE_ZONE_INVENTORY" },
  },
};
export const Review: Story = { args: { provider: "cloudflare", initialStep: "review" } };
export const PartialRecords: Story = {
  args: {
    provider: "cloudflare",
    initialStep: "review",
    records: { ...DNS_SETUP.records!, complete: false, reason: "INCOMPLETE_RECORD_INVENTORY" },
  },
};
export const AttachingExisting: Story = {
  args: {
    provider: "cloudflare",
    initialStep: "review",
    target: { ...DOMAIN_ACTIVE, zone: zone.name },
  },
};
export const WrongExistingZone: Story = {
  args: { provider: "cloudflare", initialStep: "review", target: DOMAIN_ACTIVE },
};
export const UncertainWrite: Story = {
  args: {
    provider: "cloudflare",
    initialStep: "review",
    uncertain: true,
    actionError:
      "The write reply was not confirmed. Read current domain state before trying again.",
  },
};
export const Verification: Story = {
  args: {
    provider: "cloudflare",
    initialStep: "verify",
    canVerify: true,
    verification: {
      domainId: DOMAIN_ACTIVE.id,
      domainVersion: 1,
      zoneName: zone.name,
      verificationState: "pending",
      verificationRecordName: "_astrolift-challenge.acme.example",
      verificationRecordValue: "synthetic-public-txt-proof",
    },
    binding: {
      domainId: DOMAIN_ACTIVE.id,
      domainVersion: 1,
      connectionId: connection.id,
      connectionVersion: connection.version,
      zone,
      verificationState: "pending",
      verificationRecordName: "_astrolift-challenge.acme.example",
      verificationRecordValue: "synthetic-public-txt-proof",
      dnsWritesSupported: false,
    },
  },
};
export const Route53: Story = { args: { provider: "route53", initialStep: "zone" } };
export const Route53Unavailable: Story = {
  args: {
    provider: "route53",
    initialStep: "zone",
    route53Domains: [],
    route53Error: "Installed zone metadata is unavailable.",
  },
};

export const UncertainTokenCreation: Story = {
  args: {
    provider: "cloudflare",
    initialStep: "connection",
    uncertain: true,
    connection: null,
    connections: null,
    actionError: en.domainConnections.uncertain,
  },
};
