import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { DOCS_E_EVENTS, DOCS_E_EVENTS_LONG } from "./docs-e.fixtures";
import { WEBHOOK_EVENTS } from "./webhook-events-data";
import { WebhookEventsScreen } from "./WebhookEventsScreen";

const meta: Meta<typeof WebhookEventsScreen> = {
  title: "Screens/Documentation/WebhookEventsScreen",
  component: WebhookEventsScreen,
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj<typeof WebhookEventsScreen>;

/** Static reference: there is no loading or error state. */
export const Full: Story = { args: { events: WEBHOOK_EVENTS } };

export const Few: Story = { args: { events: DOCS_E_EVENTS } };

export const Empty: Story = { args: { events: [] } };

export const LongStrings: Story = { args: { events: DOCS_E_EVENTS_LONG } };
