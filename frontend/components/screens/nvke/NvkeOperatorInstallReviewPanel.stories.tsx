import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { fakeController } from "@/components/data-table/fixtures";
import {
  NvkeOperatorInstallReviewPanel,
  type NvkeOperatorInstallReviewPanelProps,
} from "./NvkeOperatorInstallReviewPanel";
import { SITE, OPERATION } from "./fixtures";
import { OPERATOR_AGENT_AUTHORITY as authority } from "./NvkeOperatorAgentPanel.stories";

export const INSTALL_REVIEW_PROPS: NvkeOperatorInstallReviewPanelProps = {
  scopeKey: "storybook-install-review",
  site: SITE,
  operation: { ...OPERATION, operationKey: "tooling.management.install", status: "UNCERTAIN" },
  authority,
  review: {
    snapshotId: "123e4567-e89b-12d3-a456-426614174095",
    snapshotExpiresAt: "2099-11-08T20:50:00Z",
    workloadCount: 2,
    siteId: SITE.id,
    siteVersion: SITE.version,
    operationId: OPERATION.id,
    operationVersion: OPERATION.version,
    authorityId: authority.id,
    authorityVersion: authority.version,
    instanceId: authority.instanceId,
    bundleManifestSha256: authority.bundleManifestSha256,
    nativeRevision: SITE.upstreamRevision,
    nativeRunId: "123e4567-e89b-12d3-a456-426614174096",
    reviewDigest: "d".repeat(64),
    observedAt: "2026-11-08T20:40:00Z",
    originalStartedAt: "2026-11-08T20:22:39Z",
    nativeArgumentsSha256: "a".repeat(64),
    nativeExitCode: 1,
    apiReady: true,
    continuationAuthorized: false,
    reviewerSha256: "b".repeat(64),
    qualificationHelperSha256: "c".repeat(64),
    evidence: {
      guardSha256: "a".repeat(64),
      startedSha256: "b".repeat(64),
      finishedSha256: "c".repeat(64),
      stdoutSha256: "d".repeat(64),
      stderrSha256: "e".repeat(64),
    },
  },
  table: fakeController({
    rows: [
      {
        namespace: "nktp",
        kind: "Deployment",
        name: "lgtm",
        generation: "9007199254740993",
        observedGeneration: "9007199254740993",
        desiredReplicas: "1",
        readyReplicas: "1",
        updatedReplicas: "1",
        observation: "ready",
      },
      {
        namespace: "capnico-system",
        kind: "Deployment",
        name: "controller-manager",
        generation: "2",
        observedGeneration: null,
        desiredReplicas: "1",
        readyReplicas: null,
        updatedReplicas: null,
        observation: "requires-review",
      },
    ],
    totalCount: 2,
    sortEnabled: false,
  }),
  loading: false,
  pending: false,
  canReview: true,
  error: null,
  onReview: async () => true,
  onRefresh: () => {},
};
export default {
  title: "Screens/NVKE/Install review",
  component: NvkeOperatorInstallReviewPanel,
  excludeStories: ["INSTALL_REVIEW_PROPS"],
  args: INSTALL_REVIEW_PROPS,
} satisfies Meta<typeof NvkeOperatorInstallReviewPanel>;
type Story = StoryObj<typeof NvkeOperatorInstallReviewPanel>;
export const Full: Story = {};
export const Unloaded: Story = { args: { review: null, table: fakeController() } };
export const Loading: Story = {
  args: { review: null, loading: true, table: fakeController({ state: "loading" }) },
};
export const ReadOnly: Story = { args: { canReview: false } };
export const Unverified: Story = {
  args: { authority: { ...authority, verificationCode: "UNVERIFIED" } },
};
export const Failure: Story = { args: { error: "Review expired. Prepare a new snapshot." } };
export const Expired: Story = {
  args: { review: { ...INSTALL_REVIEW_PROPS.review!, snapshotExpiresAt: "2020-01-01T00:00:00Z" } },
};
export const StaleSource: Story = {
  args: { operation: { ...INSTALL_REVIEW_PROPS.operation, version: OPERATION.version + 1 } },
};
export const Paging: Story = {
  args: {
    table: fakeController({
      rows: INSTALL_REVIEW_PROPS.table.rows,
      totalCount: 113,
      hasNext: true,
      sortEnabled: false,
    }),
    review: { ...INSTALL_REVIEW_PROPS.review!, workloadCount: 113 },
  },
};
export const Empty: Story = {
  args: { table: fakeController({ rows: [], totalCount: 2, search: "missing", isFiltered: true }) },
};
export const ObservedReady: Story = {
  args: {
    table: fakeController({ rows: [INSTALL_REVIEW_PROPS.table.rows[0]], totalCount: 1 }),
    review: { ...INSTALL_REVIEW_PROPS.review!, workloadCount: 1 },
  },
};
export const Width768: Story = {
  render: (args) => (
    <div style={{ width: 768 }}>
      <NvkeOperatorInstallReviewPanel {...args} />
    </div>
  ),
};

export const LongStrings: Story = {
  args: {
    table: fakeController({
      rows: [{ ...INSTALL_REVIEW_PROPS.table.rows[0], name: "native-workload-".repeat(16) }],
      totalCount: 1,
    }),
    review: { ...INSTALL_REVIEW_PROPS.review!, workloadCount: 1 },
  },
};
