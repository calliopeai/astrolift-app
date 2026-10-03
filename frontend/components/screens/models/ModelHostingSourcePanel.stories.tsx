import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, fn, userEvent, within } from "storybook/test";
import en from "@/messages/en.json";
import { ModelHostingSourcePanel, type ModelHostingSourceProps } from "./ModelHostingSourcePanel";
const args: ModelHostingSourceProps = {
  scopeKey: "org-one:actor-one:admitted",
  allowed: true,
  authorityError: null,
  onRetryAuthority: fn(),
  connections: [],
  selectedConnection: null,
  connectionsLoading: false,
  connectionsError: null,
  connectionPage: 1,
  connectionPages: 1,
  onConnectionPage: fn(),
  onRetryConnections: fn(),
  onSelectConnection: fn(),
  onConnect: fn(async () => ({
    accepted: true as const,
    account: "operator",
    current: true,
    refreshFailed: false,
  })),
  onManualSource: fn(),
};
const meta = {
  title: "Screens/Models/ModelHostingSourcePanel",
  component: ModelHostingSourcePanel,
  args,
  parameters: { layout: "padded" },
} satisfies Meta<typeof ModelHostingSourcePanel>;
export default meta;
type Story = StoryObj<typeof meta>;
export const WriteOnlyConnection: Story = {
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByText("Connect Hugging Face", { selector: "summary" }));
    await userEvent.type(c.getByLabelText("Connection name"), "Read access");
    await userEvent.type(c.getByLabelText(en.models.shared.hosting.token), "hf_story_token");
    await expect(c.getByLabelText(en.models.shared.hosting.token)).toHaveAttribute(
      "type",
      "password"
    );
    await userEvent.click(c.getByRole("button", { name: "Connect Hugging Face" }));
    await expect(
      c.getByText(en.models.shared.hosting.connected.replace("{account}", "operator"))
    ).toBeInTheDocument();
    await expect(c.getByLabelText(en.models.shared.hosting.token)).toHaveValue("");
  },
};
export const AuthorityUnknown: Story = { args: { allowed: null, connectionsLoading: false } };
export const ConnectionReadFailed: Story = {
  args: { connectionsError: "Connection inventory is temporarily unavailable" },
};
export const PaginatedConnections: Story = {
  args: {
    connectionPage: 2,
    connectionPages: 7,
    connections: [
      { id: "safe-id", version: 1, name: "Private models", accountUsername: "literal-hf-account" },
    ],
  },
};
