import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import {
  AGENT_DRAFT,
  CONFLICT,
  EDITOR,
  EMPTY_DRAFT,
  LONG,
  LONG_APP,
  NO_REPO_APP,
  RENDER_FAILED,
} from "./app-config-manifest.fixtures";
import { ConfigEditorScreen } from "./ConfigEditorScreen";

const meta: Meta<typeof ConfigEditorScreen> = {
  title: "Screens/Apps/Config/ConfigEditorScreen",
  component: ConfigEditorScreen,
  args: EDITOR,
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj<typeof ConfigEditorScreen>;

const openCodeView = async (canvasElement: HTMLElement) => {
  const canvas = within(canvasElement);
  await userEvent.click(canvas.getByRole("button", { name: /Code/ }));
};

/** Form view (the default), the visual builder over the loaded manifest. */
export const Full: Story = {};

/** Code view: the raw TOML editor beside the rendered resources. */
export const CodeView: Story = {
  args: { isDirty: true },
  play: async ({ canvasElement }) => {
    await openCodeView(canvasElement);
    await expect(
      within(canvasElement).getByPlaceholderText("# astrolift.toml")
    ).toBeInTheDocument();
  },
};

export const Loading: Story = {
  args: { app: null, loading: true },
};

/** No app with this slug, or no permission to see it. */
export const NotFound: Story = {
  args: { app: null, slug: "no-such-app" },
};

/** An empty manifest: the builder's empty workloads state. */
export const Empty: Story = {
  args: { draft: EMPTY_DRAFT },
};

/** The error state: the manifest failed to render (Code view). */
export const RenderFailed: Story = {
  args: { rendered: RENDER_FAILED },
  play: async ({ canvasElement }) => openCodeView(canvasElement),
};

/** Code view while the rendered preview is still loading. */
export const RenderedLoading: Story = {
  args: { rendered: null, renderedLoading: true },
  play: async ({ canvasElement }) => openCodeView(canvasElement),
};

/** Code view with no rendered output. */
export const NoRenderedOutput: Story = {
  args: { rendered: null },
  play: async ({ canvasElement }) => openCodeView(canvasElement),
};

/** Someone else changed the manifest while this draft was open. */
export const Conflict: Story = {
  args: { conflict: CONFLICT, isDirty: true },
};

/** No source connection: the staged draft is applied directly. */
export const NoSourceRepo: Story = {
  args: { app: NO_REPO_APP, changedEnvKeys: ["env.FEATURE_FLAGS"] },
};

/** An agent config repo: the Form view renders the agent builder slot. */
export const AgentConfig: Story = {
  args: {
    draft: AGENT_DRAFT,
    schemaFamily: "agent_config",
    renderAgentConfigForm: ({ draft }) => (
      <pre className="bg-muted rounded p-3 font-mono text-xs">{draft}</pre>
    ),
  },
};

export const Busy: Story = {
  args: { busy: true, saving: true, isDirty: true },
};

export const LongStrings: Story = {
  args: { app: LONG_APP, slug: LONG, isDirty: true },
  play: async ({ canvasElement }) => openCodeView(canvasElement),
};
