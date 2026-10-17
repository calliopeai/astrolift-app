import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { Input } from "@/components/ui/input";

import { WizardField } from "./WizardField";

const meta: Meta<typeof WizardField> = {
  title: "Screens/Brains/New/WizardField",
  component: WizardField,
  parameters: { layout: "padded" },
  args: {
    id: "field",
    label: "Repository",
    required: true,
    help: "owner/name. A new one, or one with no commits yet.",
    children: <Input id="field" className="font-mono" defaultValue="acme/platform-brain" />,
  },
};
export default meta;

type Story = StoryObj<typeof WizardField>;

export const Help: Story = {};

/** The field's own check, in words. */
export const Problem: Story = {
  args: {
    problem: "Use owner/name, letters, digits, dots, dashes and underscores.",
    children: <Input id="field" className="font-mono" defaultValue="acme platform" aria-invalid />,
  },
};

/** The server's refusal wins over the field's own note. */
export const ServerError: Story = {
  args: {
    problem: "Use owner/name.",
    serverError: "acme/platform-brain already has commits; use an empty or new repository",
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("alert")).toHaveTextContent("already has commits");
  },
};

export const Optional: Story = { args: { required: false, help: undefined } };
