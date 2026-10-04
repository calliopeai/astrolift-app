import { useState } from "react";
import en from "@/messages/en.json";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";
import { NextIntlClientProvider } from "next-intl";
import de from "@/messages/de.json";
import ko from "@/messages/ko.json";
import { SharedModelDeploymentScreen } from "./SharedModelDeploymentScreen";
import { sharedModelRequest } from "./shared-model-form";
import { sharedDeploymentProps } from "./shared-model.fixtures";
const meta = {
  title: "Screens/Models/SharedModelDeploymentScreen",
  component: SharedModelDeploymentScreen,
  parameters: { layout: "padded" },
  args: { initialStep: 2 },
} satisfies Meta<typeof SharedModelDeploymentScreen>;
export default meta;
type Story = StoryObj<typeof meta>;
export const GPUReview: Story = {
  args: sharedDeploymentProps,
  play: async ({ canvasElement }) => {
    await userEvent.click(within(canvasElement).getByRole("button", { name: "Review deployment" }));
    const dialog = within(canvasElement.ownerDocument.body).getByRole("alertdialog");
    await expect(dialog).toHaveTextContent("Hardware capacity and model fit remain unverified");
  },
};
export const CPU: Story = {
  args: {
    ...sharedDeploymentProps,
    draft: {
      ...sharedDeploymentProps.draft,
      computeMode: "cpu",
      gpuCount: "0",
      cpuKvCacheGiB: "4",
    },
    admission: null,
  },
};
export const NoSelectedModel: Story = {
  args: { ...sharedDeploymentProps, model: null, admission: null },
};
export const RuntimeNotConfigured: Story = {
  args: {
    ...sharedDeploymentProps,
    admission: {
      ...sharedDeploymentProps.admission!,
      eligible: false,
      reason: "No certified CPU runtime is configured",
    },
  },
};
export const Verifying: Story = {
  args: { ...sharedDeploymentProps, admission: null, admissionLoading: true },
};
export const ReadError: Story = {
  args: {
    ...sharedDeploymentProps,
    admission: null,
    admissionError: "Admission verification failed",
  },
};
export const DeployRefused: Story = {
  args: {
    ...sharedDeploymentProps,
    onDeploy: async () => ({ accepted: false, message: "Target permission changed" }),
  },
  play: GPUReview.play,
};
export const German: Story = {
  args: sharedDeploymentProps,
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="de" messages={de}>
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};
export const Korean: Story = {
  args: sharedDeploymentProps,
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="ko" messages={ko}>
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};
export const Width768: Story = {
  args: {
    ...sharedDeploymentProps,
    model: {
      repoId: "publisher/very-long-production-language-model-name-with-a-complete-immutable-sha",
      revisionSha: "a".repeat(40),
    },
  },
  render: (args) => (
    <div style={{ width: 768 }}>
      <SharedModelDeploymentScreen {...args} />
    </div>
  ),
};

const localModel = {
  localArtifactId: "artifact-guid",
  expectedArtifactVersion: 2,
  name: "Verified local safetensors",
  manifestSha256: "b".repeat(64),
};
export const LocalSourceReview: Story = {
  args: {
    ...sharedDeploymentProps,
    model: localModel,
    admission: {
      ...sharedDeploymentProps.admission!,
      requestKey: JSON.stringify(
        sharedModelRequest(
          sharedDeploymentProps.organizationId,
          sharedDeploymentProps.clusters.rows[0],
          localModel,
          sharedDeploymentProps.draft
        )
      ),
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("Verified local safetensors")).toBeInTheDocument();
    await expect(
      canvas.queryByRole("link", { name: "Review access and approvals on Hugging Face" })
    ).not.toBeInTheDocument();
    await userEvent.click(canvas.getByRole("button", { name: "Review deployment" }));
    const dialog = within(canvasElement.ownerDocument.body).getByRole("alertdialog");
    await expect(dialog).toHaveTextContent("b".repeat(64));
    await expect(dialog).toHaveTextContent("Hardware capacity and model fit remain unverified");
  },
};

export const Wizard: Story = {
  args: { ...sharedDeploymentProps, initialStep: 1 },
  render: function WizardRender(args) {
    const [draft, setDraft] = useState({
      ...args.draft,
      name: "Qwen2.5-0.5B-Instruct",
      computeMode: "cpu" as const,
      gpuCount: "0",
      cpuKvCacheGiB: "2",
    });
    const [licenseReviewed, setLicenseReviewed] = useState(false);
    const request = sharedModelRequest(
      args.organizationId,
      args.clusters.rows[0],
      args.model,
      draft
    );
    return (
      <SharedModelDeploymentScreen
        {...args}
        draft={draft}
        licenseReviewed={licenseReviewed}
        onLicenseReviewed={setLicenseReviewed}
        onDraftChange={(field, value) => setDraft((current) => ({ ...current, [field]: value }))}
        admission={{ ...args.admission!, requestKey: JSON.stringify(request) }}
      />
    );
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("radio", { name: "CPU" })).toBeVisible();
    await expect(
      canvas.queryByRole("button", { name: "Review deployment" })
    ).not.toBeInTheDocument();
    await userEvent.click(
      canvas.getByRole("button", { name: en.models.shared.usability.continueStep })
    );
    await expect(canvas.queryByRole("radio", { name: "CPU" })).not.toBeInTheDocument();
    await userEvent.click(canvas.getByLabelText(en.models.shared.hosting.licenseReview));
    await expect(canvas.getByRole("button", { name: "Review deployment" })).toBeEnabled();
    await userEvent.click(
      canvas.getByRole("link", { name: en.models.shared.hosting.reviewResourceRequests })
    );
    await expect(canvas.getByLabelText(en.models.shared.placement.cpuRequest)).toHaveFocus();
    await expect(canvas.getByLabelText(en.models.shared.placement.name)).toHaveValue(
      "Qwen2.5-0.5B-Instruct"
    );
    await userEvent.click(
      canvas.getByRole("button", { name: en.models.shared.usability.continueStep })
    );
    await expect(canvas.getByLabelText(en.models.shared.hosting.licenseReview)).toBeChecked();
  },
};
