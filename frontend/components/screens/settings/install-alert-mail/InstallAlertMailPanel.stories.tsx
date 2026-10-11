import { NextIntlClientProvider } from "next-intl";
import en from "@/messages/en.json";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { defaultListState, type ListStateController } from "@/components/list/list-state";
import { emailHistoryDefinition } from "@/components/screens/email-delivery/email-delivery-history";
import {
  InstallAlertMailPanel,
  type InstallAlertMailPanelProps,
  type AlertMailTest,
} from "./InstallAlertMailPanel";
const noop = () => {};
const definition = emailHistoryDefinition("Alert tests");
const list: ListStateController = {
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
export const ALERT_TEST: AlertMailTest = {
  id: "30000000-0000-4000-8000-000000000001",
  requestId: "40000000-0000-4000-8000-000000000001",
  version: 2,
  eventKind: "deploy.failed",
  transport: "smtp",
  sender: "alerts@install.example",
  recipient: "operator@example.test",
  status: "accepted",
  reasonCode: null,
  createdAt: "2026-10-04T12:00:00Z",
  acceptedAt: "2026-10-04T12:00:01Z",
  deliveryObserved: false,
};
export const ALERT_PANEL: InstallAlertMailPanelProps = {
  event: "deploy.failed",
  onEvent: noop,
  support: {
    allowed: true,
    reason: null,
    transport: "smtp",
    sender: ALERT_TEST.sender,
    recipient: ALERT_TEST.recipient,
    tlsMode: "starttls",
    sourceFingerprint: "a".repeat(64),
    checkedAt: ALERT_TEST.createdAt,
  },
  loading: false,
  supportError: false,
  reviewed: false,
  busy: false,
  locked: false,
  canReview: true,
  canSend: false,
  requestId: null,
  current: null,
  message: null,
  canStartAnother: false,
  onReview: noop,
  onSend: noop,
  onRefresh: noop,
  onStartAnother: noop,
  history: {
    list,
    rows: [],
    loading: false,
    stale: false,
    error: null,
    totalCount: 0,
    nextCursor: null,
    refetch: noop,
  },
};
const meta: Meta<typeof InstallAlertMailPanel> = {
  title: "Screens/Settings/Install alert email",
  component: InstallAlertMailPanel,
  render: (p) => (
    <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
      <InstallAlertMailPanel {...p} />
    </NextIntlClientProvider>
  ),
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <Story />
      </NextIntlClientProvider>
    ),
  ],
  args: ALERT_PANEL,
  parameters: { layout: "padded" },
};
export default meta;
type Story = StoryObj<typeof meta>;
export const Review: Story = {};
export const Reviewed: Story = { args: { reviewed: true, canSend: true } };
export const Checking: Story = { args: { loading: true, support: null, canReview: false } };
export const ReadError: Story = { args: { supportError: true, support: null, canReview: false } };
export const Unsupported: Story = {
  args: {
    support: {
      ...ALERT_PANEL.support!,
      allowed: false,
      reason: "ALERT_MAIL_TRANSPORT_UNSUPPORTED",
      transport: null,
    },
    canReview: false,
  },
};
export const PreferenceDisabled: Story = {
  args: {
    support: { ...ALERT_PANEL.support!, allowed: false, reason: "ALERT_MAIL_PREFERENCE_DISABLED" },
    canReview: false,
  },
};
export const Unknown: Story = {
  args: {
    locked: true,
    requestId: ALERT_TEST.requestId,
    current: { ...ALERT_TEST, status: "unknown", acceptedAt: null },
    canReview: false,
    message: "uncertain",
  },
};
export const Accepted: Story = {
  args: {
    locked: true,
    canReview: false,
    current: ALERT_TEST,
    requestId: ALERT_TEST.requestId,
    canStartAnother: true,
    history: { ...ALERT_PANEL.history, rows: [ALERT_TEST], totalCount: 1 },
  },
};
export const HistoryError: Story = {
  args: { history: { ...ALERT_PANEL.history, error: { message: "Current history unavailable" } } },
};
export const Narrow: Story = { ...Accepted, parameters: { viewport: { value: "mobile1" } } };
