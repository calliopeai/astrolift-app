import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";
import {
  ModelConnectionIntakePanel,
  type ModelConnectionIntakeProps,
} from "./ModelConnectionIntakePanel";
import { fakeModelPage, subscriptionProps } from "./shared-model.fixtures";

export const connectionIntakeFixture: ModelConnectionIntakeProps = {
  scopeKey: "actor:org:model",
  deployment: subscriptionProps.deployment,
  supported: true,
  supportError: null,
  onRetrySupport: () => {},
  targets: fakeModelPage({
    rows: [
      {
        id: "env-staging",
        version: 4,
        appVersion: 7,
        clusterId: "cluster-one",
        appSlug: "storefront",
        environmentName: "staging",
        eligible: true,
        action: "REQUEST",
        policyVersion: "policy-revision",
        reason: null,
      },
    ],
  }),
  recovery: null,
  onRestoreRecovery: () => null,
  onDiscardRecovery: () => {},
  onSubmit: async () => ({ accepted: true, kind: "REQUEST", id: "request-one", version: 1 }),
  onAccepted: () => {},
};
const meta = {
  title: "Screens/Models/ModelConnectionIntake",
  component: ModelConnectionIntakePanel,
  excludeStories: ["connectionIntakeFixture"],
  parameters: { layout: "padded" },
} satisfies Meta<typeof ModelConnectionIntakePanel>;
export default meta;
type Story = StoryObj<typeof meta>;
export const ApprovalRequired: Story = {
  args: connectionIntakeFixture,
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByRole("button", { name: "storefront / staging" }));
    await userEvent.type(c.getByLabelText("Connection alias"), "chat");
    await userEvent.click(c.getByRole("button", { name: "Review connection" }));
    const dialog = within(canvasElement.ownerDocument.body).getByRole("alertdialog");
    await expect(dialog).toHaveTextContent("It creates no subscription");
  },
};
export const DirectConnect: Story = {
  args: {
    ...connectionIntakeFixture,
    targets: fakeModelPage({
      rows: [{ ...connectionIntakeFixture.targets.rows[0], action: "AUTO" }],
    }),
    onSubmit: async () => ({ accepted: true, kind: "AUTO", id: "subscription-one", version: 1 }),
  },
};
export const Denied: Story = {
  args: {
    ...connectionIntakeFixture,
    targets: fakeModelPage({
      rows: [
        {
          ...connectionIntakeFixture.targets.rows[0],
          eligible: false,
          action: "DENY",
          reason: "Current policy denies this destination",
        },
      ],
    }),
  },
};
export const Refused: Story = {
  args: {
    ...connectionIntakeFixture,
    onSubmit: async () => ({ accepted: false, message: "Policy revision changed" }),
  },
  play: ApprovalRequired.play,
};
export const SavedReadFailed: Story = {
  args: {
    ...connectionIntakeFixture,
    onAccepted: () => Promise.reject(new Error("Follow-up read unavailable")),
  },
};
export const UnsupportedServer: Story = { args: { ...connectionIntakeFixture, supported: false } };
export const Checking: Story = { args: { ...connectionIntakeFixture, supported: null } };
export const RecoverableRead: Story = {
  args: {
    ...connectionIntakeFixture,
    targets: {
      ...connectionIntakeFixture.targets,
      error: { message: "Current destination read failed" },
      stale: true,
    },
  },
};
