import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { FormPreviewTab } from "./FormPreviewTab";
import { LONG, SCHEMA } from "./forms.fixtures";

const meta: Meta = { title: "Screens/Forms/FormPreviewTab" };
export default meta;

type Story = StoryObj;

/** Every field shape the preview renders: text, email, enum, boolean, number, array, textarea, object. */
export const Full: Story = { render: () => <FormPreviewTab schema={SCHEMA} /> };

/** The preview has no loading or error state; the closest is a schema with no fields. */
export const Empty: Story = {
  render: () => <FormPreviewTab schema={{ type: "object", properties: {} }} />,
};

export const LongStrings: Story = {
  render: () => (
    <FormPreviewTab
      schema={{
        type: "object",
        title: LONG,
        description: `${LONG}, described at length.`,
        required: ["a"],
        properties: { a: { type: "string", title: LONG, description: LONG, format: "url" } },
      }}
    />
  ),
};
