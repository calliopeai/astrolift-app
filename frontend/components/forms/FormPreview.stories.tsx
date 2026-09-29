import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { FormPreview } from "@/components/forms/FormPreview";

const meta: Meta = { title: "Patterns/Forms/FormPreview" };
export default meta;

const SCHEMA = {
  type: "object",
  properties: {
    name: { type: "string", title: "Name" },
    email: { type: "string", title: "Email", format: "email" },
    urgency: { type: "string", title: "Urgency", enum: ["low", "normal", "high"] },
    notes: { type: "string", title: "Notes" },
  },
  required: ["name", "email"],
};

export const Default: StoryObj = {
  render: () => <FormPreview schema={SCHEMA} formName="Support request" />,
};
