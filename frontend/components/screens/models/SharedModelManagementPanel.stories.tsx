import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";
import { SharedModelManagementPanel } from "./SharedModelManagementPanel";
import { modelSettingsRequest } from "./shared-model-settings";
import { sharedModelManagementProps } from "./shared-model-management.fixtures";
const meta = {
  title: "Screens/Models/SharedModelManagementPanel",
  component: SharedModelManagementPanel,
  parameters: { layout: "padded" },
  args: sharedModelManagementProps,
} satisfies Meta<typeof SharedModelManagementPanel>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Owner: Story = {};
export const Reader: Story = { args: { canManage: false } };
export const Busy: Story = {
  args: { model: { ...sharedModelManagementProps.model, status: "updating" } },
};
export const UnknownRuntimeCleanup: Story = {
  args: {
    admission: null,
    model: { ...sharedModelManagementProps.model, status: "failed", runtimeSupported: null },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("button", { name: "Review resource update" })).toBeDisabled();
    await expect(canvas.getByRole("button", { name: "Review deprovisioning" })).toBeEnabled();
  },
};
export const UpdateReview: Story = {
  play: async ({ canvasElement }) => {
    await userEvent.click(
      within(canvasElement).getByRole("button", { name: "Review resource update" })
    );
    await expect(
      within(canvasElement.ownerDocument.body).getByRole("alertdialog")
    ).toHaveTextContent("temporarily lose access");
  },
};
export const DeleteReview: Story = {
  play: async ({ canvasElement }) => {
    await userEvent.click(
      within(canvasElement).getByRole("button", { name: "Review deprovisioning" })
    );
    await expect(
      within(canvasElement.ownerDocument.body).getByRole("alertdialog")
    ).toHaveTextContent("retained");
  },
};
const localEdit = {
  model: {
    ...sharedModelManagementProps.model,
    sourceKind: "local_artifact",
    localArtifactId: "artifact-one",
    localArtifactVersion: 3,
    localManifestSha256: "a".repeat(64),
    revisionSha: null,
    computeMode: "cpu",
    desiredResources: {
      cpuRequest: "2",
      memoryRequest: "8Gi",
      gpuCount: 0,
      cpuKvCacheGiB: 2,
      replicas: 1,
    },
  },
  draft: {
    ...sharedModelManagementProps.draft,
    computeMode: "cpu",
    gpuCount: "0",
    cpuKvCacheGiB: "2",
  },
} as const;
export const LocalSourceEdit: Story = {
  args: {
    ...localEdit,
    admission: {
      ...sharedModelManagementProps.admission!,
      requestKey: JSON.stringify(modelSettingsRequest(localEdit.model, localEdit.draft)),
    },
  },
};
export const DedicatedAccess: Story = {
  args: {
    access: {
      mode: "DEDICATED",
      app: { id: "app-guid", version: 8, name: "Storefront", slug: "storefront" },
    },
    admission: {
      ...sharedModelManagementProps.admission!,
      requestKey: JSON.stringify(
        modelSettingsRequest(sharedModelManagementProps.model, sharedModelManagementProps.draft, {
          mode: "DEDICATED",
          app: { id: "app-guid", version: 8, name: "Storefront", slug: "storefront" },
        })
      ),
    },
    onAccessChange: () => {},
    onSelectDedicatedApp: () => {},
  },
};
