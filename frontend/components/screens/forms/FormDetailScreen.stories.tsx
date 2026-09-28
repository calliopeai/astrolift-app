import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { FormDetailScreen } from "./FormDetailScreen";
import { DETAIL, DRAFT_FORM, LONG_FORM, LONG_SUBMISSION } from "./forms.fixtures";

const meta: Meta = {
  title: "Screens/Forms/FormDetailScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

/** A published form on the overview tab. */
export const Full: Story = { render: () => <FormDetailScreen {...DETAIL} /> };

export const PreviewTab: Story = {
  render: () => <FormDetailScreen {...DETAIL} />,
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByRole("button", { name: "Preview" }));
    await expect(c.getByText("Submit (preview)")).toBeInTheDocument();
  },
};

export const SubmissionsTab: Story = {
  render: () => <FormDetailScreen {...DETAIL} />,
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByRole("button", { name: /Submissions/ }));
    await expect(c.getByText("Last 30 days")).toBeInTheDocument();
  },
};

/** A draft: Publish instead of Fill Out / Archive, no submissions yet. */
export const Draft: Story = {
  render: () => <FormDetailScreen {...DETAIL} form={DRAFT_FORM} submissions={[]} />,
};

export const Loading: Story = {
  render: () => <FormDetailScreen {...DETAIL} form={null} loading />,
};

/** No form with this slug. */
export const NotFound: Story = {
  render: () => <FormDetailScreen {...DETAIL} slug="no-such-form" form={null} />,
};

export const LoadFailed: Story = {
  render: () => (
    <FormDetailScreen
      {...DETAIL}
      form={null}
      error={{ message: "Network error: 502 Bad Gateway" }}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <FormDetailScreen
      {...DETAIL}
      slug={LONG_FORM.slug}
      form={LONG_FORM}
      submissions={[LONG_SUBMISSION]}
    />
  ),
};
