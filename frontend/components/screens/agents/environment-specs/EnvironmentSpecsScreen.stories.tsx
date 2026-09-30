import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { useLocalListState } from "@/components/list/use-list-state";

import { ENVIRONMENT_SPECS_LIST } from "./environment-specs";
import { ENVIRONMENT_SPEC, LONG_ENVIRONMENT_SPEC } from "./environment-specs.fixtures";
import { EnvironmentSpecsScreen, type EnvironmentSpecsScreenProps } from "./EnvironmentSpecsScreen";

const meta: Meta = {
  title: "Screens/Agents/EnvironmentSpecs/EnvironmentSpecsScreen",
  parameters: { layout: "padded" },
};
export default meta;
type Story = StoryObj;
function Specs(patch: Partial<Omit<EnvironmentSpecsScreenProps, "list">>) {
  const list = useLocalListState(ENVIRONMENT_SPECS_LIST);
  return (
    <EnvironmentSpecsScreen
      list={list}
      rows={[ENVIRONMENT_SPEC]}
      totalCount={1}
      loading={false}
      error={null}
      onRetry={() => {}}
      {...patch}
    />
  );
}
export const Full: Story = {
  render: () => <Specs />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("link", { name: /Engineering Codex/ })).toHaveAttribute(
      "href",
      "/agents/environment-specs/engineering-codex"
    );
  },
};
export const Loading: Story = { render: () => <Specs rows={[]} totalCount={0} loading /> };
export const Empty: Story = { render: () => <Specs rows={[]} totalCount={0} /> };
export const LoadError: Story = {
  render: () => (
    <Specs
      rows={[]}
      totalCount={0}
      error={{ message: "The server could not read environment specs." }}
    />
  ),
};
export const RefreshError: Story = {
  render: () => <Specs error={{ message: "Refresh failed. Showing the last loaded recipes." }} />,
};
export const LongStrings: Story = { render: () => <Specs rows={[LONG_ENVIRONMENT_SPEC]} /> };
export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Specs rows={[LONG_ENVIRONMENT_SPEC, ENVIRONMENT_SPEC]} totalCount={2} />
    </div>
  ),
};
