import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { fakeController } from "@/components/data-table/fixtures";
import {
  NvkeOperatorInstallPlanPanel,
  type NvkeOperatorInstallPlanPanelProps,
} from "./NvkeOperatorInstallPlanPanel";
import { INSTALL_REVIEW_PROPS } from "./NvkeOperatorInstallReviewPanel.stories";

const source = INSTALL_REVIEW_PROPS.review!;
export const INSTALL_PLAN_PROPS: NvkeOperatorInstallPlanPanelProps = {
  scopeKey: "storybook-install-plan",
  site: INSTALL_REVIEW_PROPS.site,
  operation: INSTALL_REVIEW_PROPS.operation,
  authority: INSTALL_REVIEW_PROPS.authority,
  canReview: true,
  pending: false,
  loading: false,
  error: null,
  retainedPlanId: null,
  canRetry: true,
  onPrepare: async () => true,
  onReview: async () => true,
  onRetryPreparation: async () => true,
  plan: {
    id: "123e4567-e89b-12d3-a456-426614174097",
    version: 2,
    status: "prepared",
    operationId: source.operationId,
    operationVersion: source.operationVersion,
    siteId: source.siteId,
    siteVersion: source.siteVersion,
    authorityId: source.authorityId,
    authorityVersion: source.authorityVersion,
    itemCount: 3,
    retainedBytes: "9007199254740993",
    proposedBytes: "4096",
    plan: {
      format: 1,
      scope: "remaining-management-install-proposal",
      id: "123e4567-e89b-12d3-a456-426614174097",
      preparedAt: "2026-11-08T20:41:00Z",
      phaseEvidence: "retained-native-stderr-order-only",
      requiresOriginalSourceVerification: true,
      requiresExplicitAcknowledgement: true,
      continuationAuthorized: false,
      firstRemainingOrdinal: 1,
      planDigest: "f".repeat(64),
      source: {
        siteId: source.siteId,
        instanceId: source.instanceId,
        jobId: source.operationId,
        nativeRunId: source.nativeRunId,
        nativeRevision: source.nativeRevision,
        nativeExitCode: 1,
        bundleManifestSha256: source.bundleManifestSha256,
        nativeArgumentsSha256: source.nativeArgumentsSha256,
        retainedInputsSha256: "a".repeat(64),
        proposedInputsSha256: "b".repeat(64),
        proposedExecutionSha256: "c".repeat(64),
        privateSourceSha256: "d".repeat(64),
        reviewDigest: source.reviewDigest,
        stderrSha256: source.evidence.stderrSha256,
        originalStartedAt: source.originalStartedAt,
        reviewObservedAt: source.observedAt,
        originalDeploymentEvidence: "reconstructed-prepared-deployment",
      },
    },
    infrastructure: {
      apiReady: true,
      observedAt: source.observedAt,
      clusterUid: "123e4567-e89b-12d3-a456-426614174098",
      workloadCount: 2,
    },
  },
  itemTable: fakeController({
    sortEnabled: false,
    totalCount: 3,
    rows: [
      {
        ordinal: 0,
        name: "nktp-crds",
        type: "manifest",
        originalType: "template",
        contentSha256: "a".repeat(64),
        originalContentSha256: "b".repeat(64),
        originalObservation: "returned-before-next-observed-item",
        proposedAction: "skip",
      },
      {
        ordinal: 1,
        name: "nktp",
        type: "helm",
        originalType: "helm",
        contentSha256: "c".repeat(64),
        originalContentSha256: "d".repeat(64),
        originalObservation: "last-observed-in-unsuccessful-run",
        proposedAction: "execute",
      },
      {
        ordinal: 2,
        name: "postgresql",
        type: "helm",
        originalType: "helm",
        contentSha256: "e".repeat(64),
        originalContentSha256: "f".repeat(64),
        originalObservation: "not-observed",
        proposedAction: "execute",
      },
    ],
  }),
  workloadTable: INSTALL_REVIEW_PROPS.table,
};
export default {
  title: "Screens/NVKE/Install plan",
  component: NvkeOperatorInstallPlanPanel,
  excludeStories: ["INSTALL_PLAN_PROPS"],
  args: INSTALL_PLAN_PROPS,
} satisfies Meta<typeof NvkeOperatorInstallPlanPanel>;
type Story = StoryObj<typeof NvkeOperatorInstallPlanPanel>;
export const Full: Story = {};
export const Unloaded: Story = { args: { plan: null } };
export const Loading: Story = { args: { plan: null, loading: true } };
export const Pending: Story = { args: { pending: true } };
export const LostReply: Story = {
  args: {
    plan: null,
    retainedPlanId: INSTALL_PLAN_PROPS.plan!.id,
    error: "Preparation reply unconfirmed. Review the saved identity.",
  },
};
export const ReadOnly: Story = { args: { canReview: false } };
export const OtherReviewer: Story = { args: { canRetry: false } };
export const StaleSource: Story = {
  args: {
    operation: {
      ...INSTALL_PLAN_PROPS.operation,
      version: INSTALL_PLAN_PROPS.operation.version + 1,
    },
  },
};
export const Unverified: Story = {
  args: { authority: { ...INSTALL_PLAN_PROPS.authority!, verificationCode: "UNVERIFIED" } },
};
export const PageFailure: Story = {
  args: { itemTable: fakeController({ state: "error", sortEnabled: false }) },
};
export const Paging: Story = {
  args: {
    itemTable: fakeController({ ...INSTALL_PLAN_PROPS.itemTable, hasNext: true, totalCount: 300 }),
  },
};
export const EmptyFiltered: Story = {
  args: {
    itemTable: fakeController({
      rows: [],
      search: "missing",
      isFiltered: true,
      totalCount: 0,
      sortEnabled: false,
    }),
  },
};
export const LongStrings: Story = {
  args: {
    itemTable: fakeController({
      ...INSTALL_PLAN_PROPS.itemTable,
      rows: [{ ...INSTALL_PLAN_PROPS.itemTable.rows[0], name: "native-deployment-".repeat(7) }],
    }),
  },
};
export const Width768: Story = {
  render: (args) => (
    <div style={{ width: 768 }}>
      <NvkeOperatorInstallPlanPanel {...args} />
    </div>
  ),
};

export const OriginalPreparationRecovery: Story = {
  args: {
    plan: null,
    retainedPlanId: INSTALL_PLAN_PROPS.plan!.id,
    allowOriginalPreparation: true,
    error:
      "The saved identity could not be read. Only the original private input can be resubmitted.",
  },
};
export const ChooseAnotherPlan: Story = { args: { onChoosePlan: async () => {} } };
