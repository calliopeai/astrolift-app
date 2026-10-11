import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NextIntlClientProvider } from "next-intl";
import en from "@/messages/en.json";
import { DOMAIN_ACTIVE } from "@/components/screens/domains/domains-environments.fixtures";
import { DomainEmailScreen } from "./DomainEmailScreen";
import { EmailDeliveryPanel } from "./EmailDeliveryPanel";
import { DELIVERY_PANEL, DELIVERY_TEST } from "./EmailDeliveryPanel.stories";
const noop = () => {};
export const DOMAIN_EMAIL = {
  domain: DOMAIN_ACTIVE,
  domainLoading: false,
  domainError: null,
  apps: [
    {
      id: "b0000000-0000-4000-8000-000000000001",
      name: "Storefront",
      slug: "storefront",
      organizationSlug: DOMAIN_ACTIVE.organizationSlug!,
      version: 1,
    },
  ],
  appsLoading: false,
  appsError: null,
  appSearch: "",
  onAppSearch: noop,
  app: null,
  onApp: noop,
  appsNext: false,
  appsPrevious: false,
  onNextApps: noop,
  onPreviousApps: noop,
  services: [],
  servicesLoading: false,
  servicesError: null,
  service: null,
  onService: noop,
  servicesNext: false,
  servicesPrevious: false,
  onNextServices: noop,
  onPreviousServices: noop,
  onRefresh: noop,
  delivery: null,
  association: "unconfirmedDomain",
  probe: null,
  probeLoading: false,
  probeError: null,
  onProbe: async () => {},
  onResetProbe: noop,
} satisfies React.ComponentProps<typeof DomainEmailScreen>;
const meta: Meta<typeof DomainEmailScreen> = {
  title: "Screens/EmailDelivery/DomainEmailScreen",
  component: DomainEmailScreen,
  args: DOMAIN_EMAIL,
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
export const SelectSource: Story = {};
export const Loading: Story = { args: { domainLoading: true, domain: null } };
export const ReadFailure: Story = {
  args: { domain: null, domainError: "Current domain could not be read." },
};
export const NoApps: Story = { args: { apps: [] } };
export const EmptyServicePage: Story = {
  args: { app: DOMAIN_EMAIL.apps[0], services: [], servicesNext: true },
};
export const SourceRefused: Story = {
  args: {
    delivery: (
      <EmailDeliveryPanel
        {...DELIVERY_PANEL}
        support={{ allowed: false, reason: "EXACT_TEST_TRANSPORT_UNSUPPORTED" }}
      />
    ),
  },
};
export const ReviewedSend: Story = {
  args: { delivery: <EmailDeliveryPanel {...DELIVERY_PANEL} reviewed canSend /> },
};
export const AcceptedAndHistory: Story = {
  args: {
    delivery: (
      <EmailDeliveryPanel
        {...DELIVERY_PANEL}
        current={DELIVERY_TEST}
        history={{ ...DELIVERY_PANEL.history, rows: [DELIVERY_TEST], totalCount: 1 }}
      />
    ),
  },
};
export const DnsRecords: Story = {
  args: {
    probe: {
      state: "OK",
      perspective: "PUBLIC_RECURSIVE_DNS",
      checkedAt: "2026-10-01T00:00:00Z",
      reason: "CONTROLLED_DNS_OBSERVATION",
      hostname: DOMAIN_ACTIVE.zone,
      tool: "LOOKUP",
      recordType: "MX",
      values: ["10 mail.example.net"],
      publicAddress: null,
      httpStatus: null,
      tlsVerified: null,
      latencyMs: 2,
    },
  },
};
export const DnsNoData: Story = {
  args: { probe: { ...DnsRecords.args!.probe!, state: "UNKNOWN", values: [] } },
};
export const DnsFailed: Story = { args: { probeError: "DNS_QUERY_UNAVAILABLE" } };

export const InstallAlertEntry: Story = {
  args: { installAlertMailHref: "/settings/notifications?section=install-email" },
};
