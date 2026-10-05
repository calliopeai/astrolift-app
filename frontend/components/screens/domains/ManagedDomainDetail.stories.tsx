import { NextIntlClientProvider } from "next-intl";
import en from "@/messages/en.json";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import {
  DOMAIN_DIAGNOSTICS,
  DOMAIN_LONG,
  DOMAIN_UNPROVISIONED,
  MANAGED_DOMAIN,
} from "./domains-environments.fixtures";
import { ManagedDomainDetail, type ManagedDomainDetailProps } from "./ManagedDomainDetail";

const meta: Meta<typeof ManagedDomainDetail> = {
  title: "Screens/Domains/ManagedDomainDetail",
  component: ManagedDomainDetail,
  parameters: { layout: "fullscreen" },
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};
export default meta;
type Story = StoryObj<typeof meta>;
export const Full: Story = { args: MANAGED_DOMAIN };
export const Loading: Story = {
  args: { ...MANAGED_DOMAIN, loading: true, domain: null, diagnostics: null },
};
export const NotFound: Story = { args: { ...MANAGED_DOMAIN, domain: null, diagnostics: null } };
export const ReadFailed: Story = {
  args: {
    ...MANAGED_DOMAIN,
    domain: null,
    diagnostics: null,
    error: "Current domain admission failed.",
  },
};
export const Unprovisioned: Story = {
  args: { ...MANAGED_DOMAIN, domain: DOMAIN_UNPROVISIONED, diagnostics: null },
};
export const LongStrings: Story = {
  args: { ...MANAGED_DOMAIN, domain: DOMAIN_LONG, diagnostics: null },
};
export const Records: Story = { args: { ...MANAGED_DOMAIN, initialTab: "records" } };
export const Routing: Story = { args: { ...MANAGED_DOMAIN, initialTab: "routing" } };
export const Diagnostics: Story = { args: { ...MANAGED_DOMAIN, initialTab: "diagnostics" } };
export const Refreshing: Story = { args: { ...MANAGED_DOMAIN, diagnosticsLoading: true } };
export const ObservationFailed: Story = {
  args: {
    ...MANAGED_DOMAIN,
    diagnostics: null,
    diagnosticsError: "Configured provider zone could not be observed.",
  },
};
export const UnsupportedProvider: Story = {
  args: {
    ...MANAGED_DOMAIN,
    initialTab: "records",
    diagnostics: {
      ...DOMAIN_DIAGNOSTICS,
      providerZone: {
        ...DOMAIN_DIAGNOSTICS.providerZone,
        state: "UNSUPPORTED",
        records: [],
        reason: "This DNS driver has no provider inventory reader.",
      },
    },
  },
};
export const PartialRecords: Story = {
  args: {
    ...MANAGED_DOMAIN,
    initialTab: "records",
    diagnostics: {
      ...DOMAIN_DIAGNOSTICS,
      providerZone: { ...DOMAIN_DIAGNOSTICS.providerZone, truncated: true },
    },
  },
};
export const ReadOnly: Story = {
  args: {
    ...MANAGED_DOMAIN,
    diagnostics: {
      ...DOMAIN_DIAGNOSTICS,
      actions: { canCreate: false, canDelete: false, canRevalidate: false },
    },
  },
};
export const ProbeUnavailable: Story = {
  args: {
    ...MANAGED_DOMAIN,
    initialTab: "diagnostics",
    probe: {
      state: "UNSUPPORTED",
      perspective: "server network",
      checkedAt: DOMAIN_DIAGNOSTICS.checkedAt,
      reason: "ICMP checks are not supported by this installation.",
      hostname: DOMAIN_DIAGNOSTICS.zone,
      tool: "PING",
      recordType: "A",
      values: [],
      publicAddress: null,
      httpStatus: null,
      tlsVerified: null,
      latencyMs: null,
    },
  },
};

export const OPERATOR_REQUIRED: ManagedDomainDetailProps = {
  ...MANAGED_DOMAIN,
  initialTab: "records",
  diagnostics: {
    ...DOMAIN_DIAGNOSTICS,
    actions: { canCreate: false, canDelete: false, canRevalidate: false },
    providerZone: {
      ...DOMAIN_DIAGNOSTICS.providerZone,
      state: "UNSUPPORTED",
      reason: "PLATFORM_OPERATOR_REQUIRED",
      zoneId: null,
      zoneName: null,
      nameservers: [],
      records: [],
    },
  },
};
export const DNS_NO_DATA: ManagedDomainDetailProps = {
  ...MANAGED_DOMAIN,
  initialTab: "diagnostics",
  probe: {
    state: "OK",
    perspective: "public_dns:1.1.1.1",
    checkedAt: DOMAIN_DIAGNOSTICS.checkedAt,
    reason: "DNS_NO_DATA",
    hostname: DOMAIN_DIAGNOSTICS.zone,
    tool: "LOOKUP",
    recordType: "A",
    values: [],
    publicAddress: null,
    httpStatus: null,
    tlsVerified: null,
    latencyMs: null,
  },
};
export const ICMP_TIMEOUT: ManagedDomainDetailProps = {
  ...DNS_NO_DATA,
  probe: {
    ...DNS_NO_DATA.probe!,
    state: "UNKNOWN",
    tool: "PING",
    reason: "ICMP_TIMEOUT",
    perspective: "control_plane_network",
  },
};
export const OperatorRequired: Story = { args: OPERATOR_REQUIRED };
export const DnsNoData: Story = { args: DNS_NO_DATA };
export const IcmpTimeout: Story = { args: ICMP_TIMEOUT };
