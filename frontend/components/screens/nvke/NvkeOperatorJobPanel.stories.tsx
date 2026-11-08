import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { fakeController } from "@/components/data-table/fixtures";
import { NvkeOperatorJobPanel, type NvkeOperatorJobPanelProps } from "./NvkeOperatorJobPanel";
import { SITE, OPERATION } from "./fixtures";
import { OPERATOR_AGENT_AUTHORITY } from "./NvkeOperatorAgentPanel.stories";

export const OPERATOR_JOB_PROPS: NvkeOperatorJobPanelProps = {
  scopeKey: "storybook-original-operator-job",
  site: SITE,
  operation: { ...OPERATION, operationKey: "tooling.generate.sitespec", status: "SUCCEEDED" },
  authority: OPERATOR_AGENT_AUTHORITY,
  receipt: {
    siteId: SITE.id,
    siteVersion: SITE.version,
    operationId: OPERATION.id,
    operationVersion: OPERATION.version,
    authorityId: OPERATOR_AGENT_AUTHORITY.id,
    authorityVersion: OPERATOR_AGENT_AUTHORITY.version,
    instanceId: OPERATOR_AGENT_AUTHORITY.instanceId,
    bundleManifestSha256: OPERATOR_AGENT_AUTHORITY.bundleManifestSha256,
    nativeRevision: SITE.upstreamRevision,
    command: "generate.sitespec",
    found: true,
    state: "completed",
    effectful: false,
    cancellationRequested: false,
    nativeExitCode: 0,
    errorCode: null,
    reconciledFromNativeReceipt: false,
    receiptSha256: "d".repeat(64),
  },
  table: fakeController({
    rows: [
      { name: "stdout", size: "412", sha256: "a".repeat(64) },
      { name: "diagnostics/runner.stderr", size: "0", sha256: "b".repeat(64) },
    ],
    searchEnabled: true,
  }),
  loaded: true,
  loading: false,
  canManage: true,
  canReconcile: false,
  pending: false,
  error: null,
  progress: null,
  onLoad: () => {},
  onRefresh: () => {},
  onReconcile: async () => true,
  onDownload: async () => true,
  onCancelDownload: () => {},
};
export default {
  title: "Screens/NVKE/Operator job",
  component: NvkeOperatorJobPanel,
  excludeStories: ["OPERATOR_JOB_PROPS"],
  args: OPERATOR_JOB_PROPS,
} satisfies Meta<typeof NvkeOperatorJobPanel>;
type Story = StoryObj<typeof NvkeOperatorJobPanel>;
export const Full: Story = {};
export const Unloaded: Story = {
  args: { loaded: false, receipt: null, table: fakeController({ rows: [] }) },
};
export const Empty: Story = { args: { table: fakeController({ rows: [] }) } };
export const Loading: Story = {
  args: { loading: true, receipt: null, table: fakeController({ state: "loading" }) },
};
export const ReadOnly: Story = { args: { canManage: false } };
export const Uncertain: Story = {
  args: { operation: { ...OPERATOR_JOB_PROPS.operation, status: "UNCERTAIN" }, canReconcile: true },
};
export const MissingReceipt: Story = {
  args: {
    ...Uncertain.args,
    receipt: {
      ...OPERATOR_JOB_PROPS.receipt!,
      found: false,
      state: "not_found",
      nativeExitCode: null,
    },
    table: fakeController({ rows: [] }),
  },
};
export const Downloading: Story = {
  args: { pending: true, progress: { completed: 262144, total: 1048576 } },
};
export const Failure: Story = {
  args: { error: "The original receipt could not be verified. Refresh before another action." },
};
export const LongStrings: Story = {
  args: {
    table: fakeController({
      rows: [
        {
          name: "diagnostics/" + "long-native-artifact-name-".repeat(15) + ".json",
          size: "9007199254740993",
          sha256: "a".repeat(64),
        },
      ],
    }),
  },
};
export const Width768: Story = {
  render: (args) => (
    <div style={{ width: 768 }}>
      <NvkeOperatorJobPanel {...args} />
    </div>
  ),
};
