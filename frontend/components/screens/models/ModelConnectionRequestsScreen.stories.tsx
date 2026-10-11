import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { ModelConnectionRequestsScreen } from "./ModelConnectionRequestsScreen";
import { connectionRequestRow } from "./model-connection.fixtures";
import { fakeModelPage } from "./shared-model.fixtures";
const page = fakeModelPage({ rows: [connectionRequestRow] });
page.list.definition.searchable = false;
const meta = {
  title: "Screens/Models/ModelConnectionRequests",
  component: ModelConnectionRequestsScreen,
  args: {
    review: false,
    onReview: () => {},
    supported: true,
    supportError: null,
    onRetrySupport: () => {},
    page,
  },
  parameters: { layout: "padded" },
} satisfies Meta<typeof ModelConnectionRequestsScreen>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Own: Story = {};
export const Inbox: Story = { args: { review: true } };
export const ReadFailed: Story = {
  args: { page: { ...page, error: { message: "Reviewer access unavailable" }, stale: true } },
};
export const Empty: Story = { args: { page: fakeModelPage({ rows: [] }) } };
export const Unsupported: Story = { args: { supported: false } };
