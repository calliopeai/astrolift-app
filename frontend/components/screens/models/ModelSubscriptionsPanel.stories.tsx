import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";
import { NextIntlClientProvider } from "next-intl";
import es from "@/messages/es.json";
import ja from "@/messages/ja.json";
import { subscriptionProps, fakeModelPage } from "./shared-model.fixtures";
import { ModelSubscriptionsPanel } from "./ModelSubscriptionsPanel";

const meta = {
  title: "Screens/Models/ModelSubscriptionsPanel",
  component: ModelSubscriptionsPanel,
  parameters: { layout: "padded" },
} satisfies Meta<typeof ModelSubscriptionsPanel>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Active: Story = { args: subscriptionProps };
export const SubscribeReview: Story = {
  args: subscriptionProps,
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByRole("button", { name: "storefront / staging" }));
    await userEvent.type(c.getByLabelText("Subscription alias"), "assistant");
    await userEvent.click(c.getByRole("button", { name: "Review subscription" }));
    await expect(
      within(canvasElement.ownerDocument.body).getByRole("alertdialog")
    ).toHaveTextContent("restart");
  },
};
export const PendingRestart: Story = {
  args: {
    ...subscriptionProps,
    subscriptions: fakeModelPage({
      rows: [
        {
          ...subscriptionProps.subscriptions.rows[0],
          status: "pending",
          desiredRevision: 3,
          appliedRevision: 2,
          canRevoke: false,
        },
      ],
    }),
  },
};
export const Failed: Story = {
  args: {
    ...subscriptionProps,
    subscriptions: fakeModelPage({
      rows: [
        {
          ...subscriptionProps.subscriptions.rows[0],
          status: "failed",
          desiredRevision: 3,
          appliedRevision: 2,
          reason: "Observed generation has not reached the requested generation",
        },
      ],
    }),
  },
};
export const UnknownRuntime: Story = {
  args: {
    ...subscriptionProps,
    deployment: { ...subscriptionProps.deployment, runtimeAdmission: "unknown" },
  },
};
export const UnavailableTargets: Story = {
  args: {
    ...subscriptionProps,
    targets: fakeModelPage({ error: new Error("Target verification failed") }),
  },
};
export const Refused: Story = {
  args: {
    ...subscriptionProps,
    onSubscribe: async () => ({ accepted: false, message: "The app environment version changed" }),
  },
  play: SubscribeReview.play,
};
export const Spanish: Story = {
  args: subscriptionProps,
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="es" messages={es}>
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};
export const Japanese: Story = {
  args: subscriptionProps,
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="ja" messages={ja}>
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};
export const Width768: Story = {
  args: {
    ...subscriptionProps,
    deployment: {
      ...subscriptionProps.deployment,
      name: "organization-wide-production-model-deployment-with-a-long-immutable-name",
    },
  },
  render: (args) => (
    <div style={{ width: 768 }}>
      <ModelSubscriptionsPanel {...args} />
    </div>
  ),
};

export const NewSubscriptionsDisabled: Story = {
  args: {
    ...subscriptionProps,
    deployment: { ...subscriptionProps.deployment, subscriptionsEnabled: false },
  },
};
