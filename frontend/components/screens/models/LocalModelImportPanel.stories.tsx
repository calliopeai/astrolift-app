import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { fn, expect, userEvent, within } from "storybook/test";
import { LocalModelImportPanel, type LocalModelImportProps } from "./LocalModelImportPanel";
import { fakeModelPage } from "./shared-model.fixtures";
import en from "@/messages/en.json";
export const artifact = {
  id: "artifact-one",
  version: 2,
  name: "Local safetensors",
  state: "verified",
  manifestSha256: "b".repeat(64),
  fileCount: 3,
  sizeBytes: "1000",
};
const args: LocalModelImportProps = {
  scopeKey: "org:actor:admitted",
  allowed: true,
  phase: { kind: "idle" },
  page: fakeModelPage({ rows: [artifact] }),
  onImport: fn(async () => {}),
  onCancel: fn(),
  onUseArtifact: fn(),
};
const meta = {
  title: "Screens/Models/LocalModelImportPanel",
  component: LocalModelImportPanel,
  args,
  parameters: { layout: "padded" },
} satisfies Meta<typeof LocalModelImportPanel>;
export default meta;
type Story = StoryObj<typeof meta>;
export const VerifiedSource: Story = {
  play: async ({ canvasElement, args }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByRole("button", { name: en.models.shared.localImport.select }));
    await expect(args.onUseArtifact).toHaveBeenCalledWith(artifact);
  },
};
export const UnknownAuthority: Story = {
  args: {
    allowed: false,
    page: fakeModelPage({ rows: [], error: { message: en.models.shared.hosting.adminChecking } }),
  },
};
export const Uploading: Story = {
  args: {
    phase: { kind: "uploading", name: "model-00001-of-00020.safetensors", index: 1, count: 20 },
  },
};
export const Stopped: Story = { args: { phase: { kind: "canceled" } } };
export const SavedReadFailed: Story = {
  args: { phase: { kind: "verified", artifact, refreshFailed: true } },
};
