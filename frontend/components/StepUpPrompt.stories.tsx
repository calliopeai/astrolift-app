import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { StepUpPrompt } from "@/components/StepUpPrompt";

/** Confirm it's you before a sensitive write (#487): password or SSO. */
const meta: Meta = { title: "Shell/StepUpPrompt" };
export default meta;

const BASE = {
  open: true,
  message: null,
  error: null,
  submitting: false,
  onSubmitPassword: async () => true,
  onSso: () => {},
  onCancel: () => {},
};

export const Password: StoryObj = { render: () => <StepUpPrompt {...BASE} method="password" /> };
export const WrongPassword: StoryObj = {
  render: () => <StepUpPrompt {...BASE} method="password" error="That password is not right." />,
};
export const Sso: StoryObj = {
  render: () => (
    <StepUpPrompt {...BASE} method="sso" message="Deleting a cluster needs a fresh sign-in." />
  ),
};
