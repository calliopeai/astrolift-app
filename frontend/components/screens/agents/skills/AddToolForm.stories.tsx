import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { AddToolForm } from "./AddToolForm";
import { ADD_TOOL_FORM } from "./agent-skills.fixtures";

const meta: Meta = { title: "Screens/Agents/Skills/AddToolForm" };
export default meta;

type Story = StoryObj;

/**
 * The form has no loading, empty or error state of its own: it opens
 * blank, validation and server errors toast. Shown: the blank form and
 * the submit in flight.
 */
export const Blank: Story = { render: () => <AddToolForm {...ADD_TOOL_FORM} /> };

export const Submitting: Story = { render: () => <AddToolForm {...ADD_TOOL_FORM} loading /> };
