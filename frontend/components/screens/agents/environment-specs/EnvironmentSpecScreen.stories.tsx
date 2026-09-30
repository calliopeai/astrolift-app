import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { ENVIRONMENT_SPEC, LONG_ENVIRONMENT_SPEC } from "./environment-specs.fixtures";
import { EnvironmentSpecScreen } from "./EnvironmentSpecScreen";

const meta: Meta<typeof EnvironmentSpecScreen> = {
  title: "Screens/Agents/EnvironmentSpecs/EnvironmentSpecScreen",
  component: EnvironmentSpecScreen,
  parameters: { layout: "padded" },
  args: { spec: ENVIRONMENT_SPEC, loading: false, error: null, onRetry: () => {} },
};
export default meta;
type Story = StoryObj<typeof meta>;
export const Full: Story = {};
export const Loading: Story = { args: { spec: null, loading: true } };
export const NotFound: Story = { args: { spec: null } };
export const LoadError: Story = {
  args: { spec: null, error: { message: "The server could not read this environment spec." } },
};
export const RefreshError: Story = {
  args: { error: { message: "Refresh failed. Showing the last loaded recipe." } },
};
export const LongStrings: Story = { args: { spec: LONG_ENVIRONMENT_SPEC } };
export const Width768: Story = {
  render: (args) => (
    <div style={{ width: 768 }}>
      <EnvironmentSpecScreen {...args} spec={LONG_ENVIRONMENT_SPEC} />
    </div>
  ),
};
