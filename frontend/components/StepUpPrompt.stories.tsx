import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { StepUpPrompt } from "@/components/StepUpPrompt";

/** Confirm it's you before a sensitive write (#487): password, SSO or a step-up PIN (#2399). */
const meta: Meta = { title: "Shell/StepUpPrompt" };
export default meta;

const BASE = {
  open: true,
  message: null,
  error: null,
  submitting: false,
  fallbackMethod: "sso" as const,
  onSubmitPassword: async () => true,
  onSubmitPin: async () => true,
  onDeclinePin: () => {},
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
/** A PIN is offered first when the org allows it and the person has one. */
export const Pin: StoryObj = { render: () => <StepUpPrompt {...BASE} method="pin" /> };
export const WrongPin: StoryObj = {
  render: () => <StepUpPrompt {...BASE} method="pin" error="invalid credential" />,
};
/** A password install falls back to the password form, not the IdP. */
export const PinWithPasswordFallback: StoryObj = {
  render: () => <StepUpPrompt {...BASE} method="pin" fallbackMethod="password" />,
};
