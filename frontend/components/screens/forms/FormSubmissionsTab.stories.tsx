import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { FormSubmissionsTab } from "./FormSubmissionsTab";
import { LONG_SUBMISSION, MANY_SUBMISSIONS, SUBMISSIONS } from "./forms.fixtures";

const meta: Meta = { title: "Screens/Forms/FormSubmissionsTab" };
export default meta;

type Story = StoryObj;

export const Full: Story = {
  render: () => <FormSubmissionsTab submissions={SUBMISSIONS} loading={false} />,
};

/** Past one page: the pager shows. */
export const Paged: Story = {
  render: () => <FormSubmissionsTab submissions={MANY_SUBMISSIONS} loading={false} />,
};

export const Loading: Story = {
  render: () => <FormSubmissionsTab submissions={[]} loading />,
};

/** The tab has no error state of its own; empty is the closest. */
export const Empty: Story = {
  render: () => <FormSubmissionsTab submissions={[]} loading={false} />,
};

/** Long values, multi-line text and a nested object, with the detail sheet open. */
export const LongStrings: Story = {
  render: () => <FormSubmissionsTab submissions={[LONG_SUBMISSION]} loading={false} />,
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByText("v3"));
    await expect(within(document.body).getByText("Submission detail")).toBeInTheDocument();
  },
};
