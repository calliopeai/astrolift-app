import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { AccessLandingPanel } from "./AccessLandingClient";
const meta = {
  title: "Screens/Administration/Access/MembershipLanding",
  component: AccessLandingPanel,
  args: { loading: false, error: null, onRetry: () => {} },
} satisfies Meta<typeof AccessLandingPanel>;
export default meta;
export const Unavailable: StoryObj<typeof meta> = {};
export const Loading: StoryObj<typeof meta> = { args: { loading: true } };
export const Failed: StoryObj<typeof meta> = {
  args: { error: "Current team access could not be confirmed" },
};
