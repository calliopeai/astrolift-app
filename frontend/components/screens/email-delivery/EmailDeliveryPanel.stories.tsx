import { NextIntlClientProvider } from "next-intl";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { defaultListState, type ListStateController } from "@/components/list/list-state";
import { emailHistoryDefinition } from "./email-delivery-history";
import en from "@/messages/en.json";
import {
  EmailDeliveryPanel,
  type EmailDeliveryPanelProps,
  type DeliveryTest,
  type DeliveryHistory,
} from "./EmailDeliveryPanel";

const noop = () => {};
const definition = emailHistoryDefinition(en.emailDelivery.history);
const storyList: ListStateController = {
  definition,
  state: defaultListState(definition),
  filters: {},
  isFiltered: false,
  setSearch: noop,
  setFilter: noop,
  applySearch: noop,
  clearFilters: noop,
  toggleSort: noop,
  setSort: noop,
  setPage: noop,
  older: noop,
  newer: noop,
  hasNewer: false,
  setPageSize: noop,
  viewHref: () => "#",
  hiddenColumns: [],
  toggleColumn: noop,
  mode: "list",
  setMode: noop,
};
function fakeHistory(overrides: Partial<DeliveryHistory> = {}): DeliveryHistory {
  return {
    list: storyList,
    rows: [],
    loading: false,
    stale: false,
    error: null,
    totalCount: 0,
    nextCursor: null,
    refetch: noop,
    ...overrides,
  };
}
export const DELIVERY_TEST: DeliveryTest = {
  id: "30000000-0000-4000-8000-000000000001",
  managedServiceId: "40000000-0000-4000-8000-000000000001",
  version: 2,
  requestId: "50000000-0000-4000-8000-000000000001",
  sender: "sender@acme.example",
  recipient: "success@simulator.amazonses.com",
  status: "accepted",
  accountId: "123456789012",
  region: "us-west-2",
  identity: "acme.example",
  transport: "aws_ses",
  providerMessageId: "message-1",
  eventTrackingConfigured: true,
  simulator: true,
  createdAt: "2026-10-04T12:00:00Z",
  acceptedAt: "2026-10-04T12:00:01Z",
  observedAt: null,
  reasonCode: null,
};
export const DELIVERY_PANEL: EmailDeliveryPanelProps = {
  serviceName: "Application mail / production",
  support: {
    allowed: true,
    reason: null,
    serviceVersion: 7,
    sender: "sender@acme.example",
    identity: "acme.example",
    accountId: "123456789012",
    region: "us-west-2",
  },
  supportLoading: false,
  supportError: null,
  draft: { recipient: "", subject: "", body: "" },
  onDraft: noop,
  reviewed: false,
  canReview: false,
  canSend: false,
  busy: false,
  locked: false,
  recovered: false,
  requestId: null,
  current: null,
  message: null,
  actionError: null,
  canStartAnother: false,
  onReview: noop,
  onSend: noop,
  onStartAnother: noop,
  onRefresh: noop,
  history: fakeHistory({
    rows: [DELIVERY_TEST],
    totalCount: 1,
  }),
};
const meta: Meta<typeof EmailDeliveryPanel> = {
  title: "Screens/EmailDelivery/EmailDeliveryPanel",
  component: EmailDeliveryPanel,
  parameters: { layout: "padded" },
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <Story />
      </NextIntlClientProvider>
    ),
  ],
  args: DELIVERY_PANEL,
};
export default meta;
type Story = StoryObj<typeof meta>;
export const NeedsReview: Story = {
  args: {
    draft: { recipient: "success@simulator.amazonses.com", subject: "", body: "" },
    canReview: true,
  },
};
export const Reviewed: Story = { args: { ...NeedsReview.args, reviewed: true, canSend: true } };
export const Checking: Story = { args: { support: null, supportLoading: true } };
export const PermissionDenied: Story = {
  args: {
    support: {
      allowed: false,
      reason: "Current permission does not support this test.",
      serviceVersion: null,
      sender: null,
      identity: null,
      accountId: null,
      region: null,
    },
  },
};
export const UnsupportedTransport: Story = {
  args: {
    support: {
      ...DELIVERY_PANEL.support!,
      allowed: false,
      reason: "EXACT_TEST_TRANSPORT_UNSUPPORTED",
    },
  },
};
export const SupportReadFailure: Story = {
  args: { support: null, supportError: "read unavailable" },
};
export const UnknownReply: Story = {
  args: {
    locked: true,
    message: "uncertain",
    requestId: DELIVERY_TEST.requestId,
    draft: { recipient: DELIVERY_TEST.recipient, subject: "", body: "" },
  },
};
export const Accepted: Story = {
  args: {
    locked: true,
    requestId: DELIVERY_TEST.requestId,
    current: DELIVERY_TEST,
    canStartAnother: true,
  },
};
export const WithoutTracking: Story = {
  args: {
    ...Accepted.args,
    current: { ...DELIVERY_TEST, eventTrackingConfigured: false, simulator: false },
  },
};
export const ObservedDelivery: Story = {
  args: {
    ...Accepted.args,
    current: { ...DELIVERY_TEST, status: "delivered", observedAt: "2026-10-04T12:01:00Z" },
    history: fakeHistory({
      rows: [{ ...DELIVERY_TEST, status: "delivered" }],
      totalCount: 1,
    }),
  },
};
export const MixedProviderObservations: Story = {
  args: {
    history: fakeHistory({
      rows: [
        "accepted",
        "delivered",
        "bounced",
        "complained",
        "deferred",
        "rejected",
        "observation_timed_out",
        "unknown",
      ].map((status, index) => ({ ...DELIVERY_TEST, id: `test-${index}`, status })),
      totalCount: 8,
    }),
  },
};
export const EmptyHistory: Story = {
  args: {
    history: fakeHistory({
      rows: [],

      totalCount: 0,
    }),
  },
};
export const HistoryReadFailure: Story = {
  args: {
    history: fakeHistory({
      error: new Error("Current history is unavailable."),
    }),
  },
};

export const RecoveredRequest: Story = {
  args: { recovered: true, requestId: DELIVERY_TEST.requestId, message: "uncertain" },
};
