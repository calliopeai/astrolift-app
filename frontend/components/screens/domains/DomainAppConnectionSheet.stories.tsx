import { NextIntlClientProvider } from "next-intl";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import en from "@/messages/en.json";
import {
  DomainAppConnectionSheet,
  type DomainAppConnectionSheetProps,
} from "./DomainAppConnectionSheet";

const target = {
  key: "reviewed-target",
  app: "Sample app",
  environment: "production",
  workload: "web",
  cluster: "AWS cluster",
};
export const APP_CONNECTION: DomainAppConnectionSheetProps = {
  open: true,
  onOpenChange: () => {},
  zone: "sample.example",
  hostnameKind: "apex",
  subdomain: "",
  onHostname: () => {},
  hostnameValid: true,
  targets: [target],
  targetKey: target.key,
  onTarget: () => {},
  readState: "ready",
  readReason: null,
  onRefresh: () => {},
  hasPreviousPage: false,
  hasNextPage: false,
  review: {
    hostname: "sample.example",
    target,
    records: ["sample.example. A ALIAS reviewed-alb.example"],
    mode: "managed",
    ingress: "Observed target",
    tls: "Pending",
    checkedAt: "2026-10-04T10:00:00Z",
    reason: null,
  },
  reviewing: false,
  reviewError: null,
  canReview: true,
  onReview: () => {},
  canPrepare: false,
  canAttach: true,
  onAttach: () => {},
  busy: false,
  outcome: "none",
  onRecover: () => {},
};
const meta: Meta<typeof DomainAppConnectionSheet> = {
  title: "Screens/Domains/DomainAppConnectionSheet",
  component: DomainAppConnectionSheet,
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
export const Apex: Story = { args: APP_CONNECTION };
export const Subdomain: Story = {
  args: { ...APP_CONNECTION, hostnameKind: "subdomain", subdomain: "app", review: null },
};
export const Loading: Story = {
  args: {
    ...APP_CONNECTION,
    initialStep: 1,
    readState: "loading",
    targets: [],
    review: null,
    canReview: false,
    canAttach: false,
  },
};
export const NoEligibleTargets: Story = {
  args: {
    ...APP_CONNECTION,
    initialStep: 1,
    targets: [],
    targetKey: null,
    review: null,
    canReview: false,
    canAttach: false,
  },
};
export const Refused: Story = {
  args: {
    ...APP_CONNECTION,
    initialStep: 1,
    readState: "refused",
    readReason: "PLATFORM_OPERATOR_REQUIRED",
    canReview: false,
    canAttach: false,
  },
};
export const ReadFailure: Story = {
  args: {
    ...APP_CONNECTION,
    initialStep: 1,
    readState: "error",
    readReason: "TARGET_READ_UNAVAILABLE",
    canReview: false,
    canAttach: false,
  },
};
export const ManagedReview: Story = { args: { ...APP_CONNECTION, initialStep: 2 } };
export const ManualRecords: Story = {
  args: {
    ...APP_CONNECTION,
    initialStep: 2,
    review: { ...APP_CONNECTION.review!, mode: "manual" },
    canAttach: false,
  },
};
export const PreparationRequired: Story = {
  args: {
    ...APP_CONNECTION,
    initialStep: 2,
    review: { ...APP_CONNECTION.review!, reason: "DOMAIN_CERTIFICATE_PENDING" },
    canPrepare: true,
    onPrepare: () => {},
    canAttach: false,
  },
};
export const Checking: Story = {
  args: { ...APP_CONNECTION, initialStep: 2, review: null, reviewing: true, canAttach: false },
};
export const ReviewFailed: Story = {
  args: {
    ...APP_CONNECTION,
    initialStep: 2,
    review: null,
    reviewError: "Current ingress could not be verified.",
    canAttach: false,
  },
};
export const UnknownReply: Story = {
  args: { ...APP_CONNECTION, outcome: "unknown", canAttach: false },
};
export const RecordedPending: Story = {
  args: { ...APP_CONNECTION, outcome: "pending", canAttach: false },
};
export const MoreTargets: Story = {
  args: { ...APP_CONNECTION, initialStep: 1, hasNextPage: true, onNextPage: () => {} },
};
