import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, fn, userEvent, within } from "storybook/test";

import { IDP_LOCAL, IDP_NOT_READY, LOGIN, LONG_TEXT } from "../shell/root-bare-login.fixtures";

import { LoginScreen } from "./LoginScreen";

const meta: Meta<typeof LoginScreen> = {
  title: "Screens/Auth/LoginScreen",
  component: LoginScreen,
  args: { ...LOGIN, continueWithIdp: fn(), submitLocal: fn(async () => {}) },
};
export default meta;

type Story = StoryObj<typeof LoginScreen>;

/** An external IdP (Cognito): one CTA. */
export const ExternalIdp: Story = {};

/** kind=local: the username + password form. */
export const LocalForm: Story = {
  args: { idp: IDP_LOCAL },
  play: async ({ canvasElement, args }) => {
    const canvas = within(canvasElement);
    await userEvent.type(canvas.getByLabelText("Username or email"), "leo");
    await userEvent.type(canvas.getByLabelText("Password"), "hunter2");
    await userEvent.click(canvas.getByRole("button", { name: "Sign in" }));
    await expect(args.submitLocal).toHaveBeenCalledWith("leo", "hunter2");
  },
};

/** Waiting on active-idp.json. */
export const Loading: Story = { args: { idp: null } };

/** No IdP configured: the closest thing this screen has to empty. */
export const NotConfigured: Story = { args: { idp: IDP_NOT_READY } };

export const NotConfiguredWithHint: Story = {
  args: { idp: { ...IDP_NOT_READY, hint: "Run `astro idp set` on the control plane." } },
};

export const LoadError: Story = {
  args: { idp: null, loadError: "TypeError: Failed to fetch" },
};

/** NEXT_PUBLIC_DEV_LOGIN=1 builds redirect straight to dev-login. */
export const DevLogin: Story = { args: { devLogin: true, idp: null } };

/** An unknown kind falls back to the IdP's own display name. */
export const LongStrings: Story = {
  args: {
    idp: {
      kind: "custom",
      displayName: "Platform Team Shared Enterprise Identity Federation Gateway",
      loginPath: "/app/auth1/login",
      ready: true,
    },
    loadError: null,
  },
};

export const LongLoadError: Story = { args: { idp: null, loadError: LONG_TEXT } };
