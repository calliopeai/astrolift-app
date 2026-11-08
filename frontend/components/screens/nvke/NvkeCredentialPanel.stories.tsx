import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { fakeController } from "@/components/data-table/fixtures";
import { NvkeCredentialPanel, type NvkeCredentialPanelProps } from "./NvkeCredentialPanel";
import { SITE, OPERATION } from "./fixtures";
export const CREDENTIAL_PROPS: NvkeCredentialPanelProps = {
  observation: {
    siteId: SITE.id,
    operationId: OPERATION.id,
    operationVersion: 12,
    sourceSiteVersion: 7,
    clusterId: "123e4567-e89b-12d3-a456-426614174092",
    sourceOperationId: OPERATION.id,
    sourceVersion: 8,
    authorityId: "123e4567-e89b-12d3-a456-426614174091",
    authorityVersion: 2,
    scrapedAt: "2026-11-08T12:00:00Z",
    clusterReported: true,
    collectionSucceeded: false,
    monitoringEnabled: false,
    renewalAutomationEnabled: null,
    warningWindowSeconds: "86400",
  },
  certificates: fakeController({
    rows: [
      {
        key: "certificate-1",
        node: "cp-1",
        credential: "super-admin",
        required: false,
        latestInspectionAvailable: false,
        observationAvailable: true,
        observationAgeSeconds: "7200.125",
        reportedNotAfter: "2026-11-01T00:00:00Z",
        reportedExpiredAtScrape: true,
      },
    ],
    totalCount: 1,
    sortEnabled: false,
  }),
  samples: fakeController({
    rows: [
      {
        key: "metric-1",
        metric: "serving_csr_count",
        node: null,
        credential: null,
        state: "pending",
        value: "9007199254740993",
      },
    ],
    totalCount: 1,
    sortEnabled: false,
  }),
  loading: false,
  error: null,
  onRefresh: () => {},
};
export default {
  title: "Screens/NVKE/Credential observations",
  component: NvkeCredentialPanel,
  excludeStories: ["CREDENTIAL_PROPS"],
  args: CREDENTIAL_PROPS,
} satisfies Meta<typeof NvkeCredentialPanel>;
type Story = StoryObj<typeof NvkeCredentialPanel>;
export const RetainedExpiry: Story = {};
export const Missing: Story = {
  args: {
    observation: {
      ...CREDENTIAL_PROPS.observation!,
      clusterReported: false,
      collectionSucceeded: false,
      monitoringEnabled: null,
      warningWindowSeconds: null,
    },
    certificates: fakeController({ state: "empty", sortEnabled: false }),
    samples: fakeController({ state: "empty", sortEnabled: false }),
  },
};
export const Loading: Story = { args: { observation: null, loading: true } };
export const Failure: Story = { args: { error: "The historical observation could not be read." } };
export const PageFailure: Story = {
  args: { certificates: fakeController({ state: "error", sortEnabled: false }) },
};
export const LongStrings: Story = {
  args: {
    certificates: fakeController({
      rows: [
        { ...CREDENTIAL_PROPS.certificates.rows[0], node: "long-control-plane-node-".repeat(15) },
      ],
      totalCount: 1,
      sortEnabled: false,
    }),
  },
};
export const Width768: Story = {
  render: (args) => (
    <div style={{ width: 768 }}>
      <NvkeCredentialPanel {...args} />
    </div>
  ),
};
