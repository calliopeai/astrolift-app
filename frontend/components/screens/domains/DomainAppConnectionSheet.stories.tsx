import { NextIntlClientProvider } from "next-intl";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import en from "@/messages/en.json";
import {
  DomainAppConnectionSheet,
  type DomainAppConnectionSheetProps,
  type RouteTargetView,
} from "./DomainAppConnectionSheet";

const target: RouteTargetView = {
  kind: "DEPLOYMENT",
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
    recordState: "proposed",
    certificate: "Not observed",
    mode: "managed",
    endpoint: "Observed target",
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
    reviewError: "Current endpoint could not be verified.",
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

const httpApiTarget: RouteTargetView = {
  kind: "FAAS_HTTP_API",
  key: "reviewed-http-api-target",
  app: "Sample function app",
  environment: "production",
  workload: "public-handler",
  cluster: "AWS cluster",
};
export const HTTP_API_CONNECTION: DomainAppConnectionSheetProps = {
  ...APP_CONNECTION,
  targets: [httpApiTarget],
  targetKey: httpApiTarget.key,
  review: {
    ...APP_CONNECTION.review!,
    target: httpApiTarget,
    endpoint: "https://sample.execute-api.us-west-2.amazonaws.com",
    records: [],
    recordState: "pending",
    api: {
      id: "sample-api",
      stage: "$default",
      functionReference: "arn:aws:lambda:us-west-2:123456789012:function:sample-handler",
    },
    certificate: "Pending",
    tls: "HTTPS not observed",
    reason: "DOMAIN_CERTIFICATE_PENDING",
  },
  canAttach: false,
};
export const HttpApiTarget: Story = { args: { ...HTTP_API_CONNECTION, initialStep: 1 } };
export const HttpApiReview: Story = { args: { ...HTTP_API_CONNECTION, initialStep: 2 } };
export const HttpApiPreparation: Story = {
  args: { ...HTTP_API_CONNECTION, initialStep: 2, canPrepare: true, onPrepare: () => {} },
};
export const HttpApiManual: Story = {
  args: {
    ...HTTP_API_CONNECTION,
    initialStep: 2,
    review: { ...HTTP_API_CONNECTION.review!, mode: "manual" },
  },
};
export const HttpApiUnknown: Story = { args: { ...HTTP_API_CONNECTION, outcome: "unknown" } };

export const HttpApiConnectReview: Story = {
  args: {
    ...HTTP_API_CONNECTION,
    initialStep: 2,
    review: {
      ...HTTP_API_CONNECTION.review!,
      certificate: "Issued",
      reason: "NATIVE_DOMAIN_ALLOCATION_PENDING",
    },
    canAttach: true,
  },
};
export const HttpApiSourceUnavailable: Story = {
  args: {
    ...HTTP_API_CONNECTION,
    initialStep: 2,
    review: {
      ...HTTP_API_CONNECTION.review!,
      recordState: "unavailable",
      api: null,
      endpoint: "Not observed",
    },
    canAttach: false,
  },
};
