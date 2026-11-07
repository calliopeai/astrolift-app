import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NvkeResourceActionPanel } from "./NvkeResourceActionPanel";
import { ACTIONS, SITE_DETAIL, SITE, OPERATION } from "./fixtures";
const update = {
  ...ACTIONS[1],
  key: "updateCluster",
  verb: "PATCH",
  path: "/clusters/{clusterID}",
};
export default {
  title: "Screens/NVKE/Resource actions",
  component: NvkeResourceActionPanel,
  args: {
    siteName: SITE.name,
    siteVersion: SITE.version,
    target: "research-cluster",
    actions: [update, ACTIONS[2]],
    selectedKey: update.key,
    access: SITE_DETAIL.access,
    sourceReady: true,
    pending: false,
    error: null,
    recovery: null,
    editor: null,
    schemaText: "{}",
    onSelect: () => {},
    onLoad: async () => true,
    onRefresh: () => {},
    onPrepare: async () => true,
    onRecover: async () => true,
    onRequest: async () => true,
  },
} satisfies Meta<typeof NvkeResourceActionPanel>;
type Story = StoryObj<typeof NvkeResourceActionPanel>;
const editor = {
  id: "template-1",
  siteVersion: SITE.version,
  operationKey: update.key,
  parametersText: '{"body":{"desiredCount":9007199254740993}}',
  preparationActionKey: null,
  concurrency: "observed_only" as const,
  source: { operationId: OPERATION.id, version: 7, resourceKey: "signed-resource-key" },
};
export const BeforeLoad: Story = {};
export const Editing: Story = { args: { editor } };
export const Conditional: Story = {
  args: { editor: { ...editor, concurrency: "native_version" } },
};
export const PrepareRead: Story = {
  args: { editor: { ...editor, parametersText: null, preparationActionKey: "getCluster" } },
};
export const ReadOnly: Story = {
  args: { access: { read: true, manage: false, admin: false, loading: false } },
};
export const Pending: Story = { args: { editor, pending: true } };
export const Failed: Story = {
  args: { editor, error: "The reviewed resource changed. Read it again before editing." },
};
export const Recovery: Story = {
  args: {
    editor,
    recovery: {
      requestId: OPERATION.requestId,
      operationKey: update.key,
      expectedSiteVersion: 7,
      timeoutSeconds: 7200,
    },
  },
};
export const LongStrings: Story = {
  args: {
    editor,
    target: "native-resource-name-".repeat(24),
    siteName: "research-site-".repeat(24),
  },
};
export const Width768: Story = {
  render: (args) => (
    <div style={{ width: 768 }}>
      <NvkeResourceActionPanel {...args} editor={editor} />
    </div>
  ),
};
