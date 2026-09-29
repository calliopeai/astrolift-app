import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { FormBuilder } from "@/components/forms/FormBuilder";

const meta: Meta = { title: "Patterns/Forms/FormBuilder", parameters: { layout: "padded" } };
export default meta;

const SCHEMA = {
  type: "object",
  properties: {
    name: { type: "string", title: "Name" },
    urgency: { type: "string", title: "Urgency", enum: ["low", "normal", "high"] },
  },
};

export const Default: StoryObj = {
  render: () => <FormBuilder schema={SCHEMA} onSave={() => {}} />,
};
export const Empty: StoryObj = {
  render: () => <FormBuilder schema={{ type: "object", properties: {} }} onSave={() => {}} />,
};
