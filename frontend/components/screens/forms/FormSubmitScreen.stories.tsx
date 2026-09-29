import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { FORM, LONG_FORM, SUBMIT } from "./forms.fixtures";
import { FormSubmitScreen } from "./FormSubmitScreen";

const meta: Meta = {
  title: "Screens/Forms/FormSubmitScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

export const Full: Story = { render: () => <FormSubmitScreen {...SUBMIT} /> };

export const Loading: Story = {
  render: () => <FormSubmitScreen {...SUBMIT} formDef={null} loading />,
};

/** No published form with this slug; DynamicForm has no separate empty state. */
export const NotFound: Story = {
  render: () => <FormSubmitScreen {...SUBMIT} slug="no-such-form" formDef={null} />,
};

export const LoadFailed: Story = {
  render: () => (
    <FormSubmitScreen {...SUBMIT} formDef={null} error="Network error: 502 Bad Gateway" />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <FormSubmitScreen
      {...SUBMIT}
      slug={LONG_FORM.slug}
      formDef={{ ...LONG_FORM, schema: { ...FORM.schema, title: LONG_FORM.name } }}
    />
  ),
};
