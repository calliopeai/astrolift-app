import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NextIntlClientProvider } from "next-intl";
import { expect, userEvent, within } from "storybook/test";
import fr from "@/messages/fr.json";
import zh from "@/messages/zh-Hans.json";
import { SharedModelsScreen } from "./SharedModelsScreen";
import { sharedModelsProps, localizedSharedModelsProps } from "./shared-models.fixtures";
const meta = {
  title: "Screens/Models/SharedModelsScreen",
  component: SharedModelsScreen,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof SharedModelsScreen>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Full: Story = {
  args: sharedModelsProps,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("link", { name: "Host a model" })).toHaveAttribute(
      "href",
      "/models/deploy"
    );
    await expect(
      canvas.getByRole("link", { name: "App, project and cloud endpoints" })
    ).toHaveAttribute("href", "/models/endpoints");
    await userEvent.click(canvas.getByRole("button", { name: "Filter by cluster Production" }));
  },
};
export const WithInstallManagedModel: Story = {
  args: {
    ...sharedModelsProps,
    installModel: { modelId: "Qwen/Qwen2.5-7B-Instruct", replicas: 1 },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("region", { name: "Managed by the install" })).toBeVisible();
    await expect(canvas.getByText("Qwen/Qwen2.5-7B-Instruct")).toBeVisible();
  },
};
export const InstallManagedModelOnly: Story = {
  args: {
    page: { ...sharedModelsProps.page, rows: [], totalCount: 0 },
    installModel: { modelId: "Qwen/Qwen2.5-7B-Instruct", replicas: null },
  },
};
export const Loading: Story = {
  args: { page: { ...sharedModelsProps.page, rows: [], loading: true } },
};
export const Empty: Story = {
  args: { page: { ...sharedModelsProps.page, rows: [], totalCount: 0 } },
};
export const Unavailable: Story = {
  args: {
    page: {
      ...sharedModelsProps.page,
      rows: [],
      error: { message: "Shared model reads are unavailable" },
    },
  },
};
export const Stale: Story = { args: { page: { ...sharedModelsProps.page, stale: true } } };
export const UnknownComputeAndReadiness: Story = {
  args: {
    page: {
      ...sharedModelsProps.page,
      rows: [
        {
          ...sharedModelsProps.page.rows[0],
          computeMode: null,
          ready: null,
          readinessObservedAt: null,
        },
      ],
    },
  },
};
export const French: Story = {
  args: localizedSharedModelsProps(fr.models.shared.deployments),
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="fr" messages={fr} timeZone="UTC">
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};
export const Chinese: Story = {
  args: localizedSharedModelsProps(zh.models.shared.deployments),
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="zh-Hans" messages={zh} timeZone="UTC">
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};
export const Width768: Story = {
  args: {
    page: {
      ...sharedModelsProps.page,
      rows: [
        {
          ...sharedModelsProps.page.rows[0],
          name: "Production-shared-organization-model-with-a-very-long-name",
          modelRepo: "organization/very-long-generation-model-with-immutable-revision",
        },
      ],
    },
  },
  render: (args) => (
    <div style={{ width: 768 }}>
      <SharedModelsScreen {...args} />
    </div>
  ),
};
