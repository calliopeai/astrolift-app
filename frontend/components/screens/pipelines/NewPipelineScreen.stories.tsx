import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";
import { NewPipelineScreen } from "./NewPipelineScreen";

const meta: Meta<typeof NewPipelineScreen> = {
  title: "Screens/Pipelines/NewPipelineScreen",
  component: NewPipelineScreen,
  args: {
    draft: {
      name: "Build",
      repoUrl: "git@github.com:acme/build.git",
      defaultBranch: "main",
      tomlPath: "",
    },
    onChange: () => {},
    onSubmit: async () => {},
    saving: false,
    allowed: true,
    error: null,
    createdId: null,
  },
};
export default meta;
type Story = StoryObj<typeof meta>;
export const Full: Story = {};
export const Failed: Story = { args: { error: "A pipeline with this name already exists." } };
export const Saving: Story = { args: { saving: true } };
export const Restricted: Story = {
  args: { allowed: false },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).queryByRole("button", { name: "Create pipeline" })
    ).toBeNull();
  },
};
export const Committed: Story = {
  args: { createdId: "pipeline-guid" },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole("link", { name: "Open pipeline" })
    ).toHaveAttribute("href", "/pipelines/pipeline-guid");
  },
};
export const LongStrings: Story = {
  args: {
    draft: {
      name: "Build".repeat(35),
      repoUrl: "git@github.com:acme/" + "repository".repeat(30) + ".git",
      defaultBranch: "feature/" + "branch".repeat(30),
      tomlPath: "pipelines/" + "directory/".repeat(30) + "astrolift.toml",
    },
  },
};
export const Width768: Story = {
  decorators: [
    (Story) => (
      <div className="w-[768px] max-w-full">
        <Story />
      </div>
    ),
  ],
};
