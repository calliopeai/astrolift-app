import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  LONG,
  LONG_APP,
  LONG_PODS,
  SHELL,
  SHELL_UPLOADED,
} from "./app-observability-shell-topology-commands.fixtures";
import { ShellScreen } from "./ShellScreen";

const meta: Meta = {
  title: "Screens/Apps/Tools/ShellScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

export const Full: Story = {
  render: () => <ShellScreen {...SHELL} {...SHELL_UPLOADED} />,
};

export const Loading: Story = {
  render: () => <ShellScreen {...SHELL} app={null} loading />,
};

/** No app with this slug, or no permission to see it. */
export const NotFound: Story = {
  render: () => <ShellScreen {...SHELL} slug="no-such-app" app={null} />,
};

/** No pods running: the terminal card shows the deploy-first empty state. */
export const Empty: Story = {
  render: () => (
    <ShellScreen
      {...SHELL}
      podRows={[]}
      selectedPod={null}
      podContainers={[]}
      selectedContainer={null}
      noPods
    />
  ),
};

/** Pods still loading: pickers disabled, no target yet. */
export const PodsLoading: Story = {
  render: () => (
    <ShellScreen
      {...SHELL}
      podRows={[]}
      selectedPod={null}
      podContainers={[]}
      selectedContainer={null}
      podsLoading
    />
  ),
};

/**
 * Upload failures surface as a toast from the hook; the closest on-screen
 * state is the upload in flight.
 */
export const Uploading: Story = {
  render: () => <ShellScreen {...SHELL} uploading />,
};

export const LongStrings: Story = {
  render: () => (
    <ShellScreen
      {...SHELL}
      slug={LONG_APP.slug}
      app={LONG_APP}
      podRows={LONG_PODS}
      selectedPod={LONG_PODS[0].name}
      podContainers={[LONG]}
      selectedContainer={LONG}
      uploadedFile={{ name: `${LONG}.py`, publicUrl: `https://uploads.example.com/${LONG}.py` }}
      uploadedCommand={`python /tmp/${LONG}.py`}
      fetchCommand={`curl -fsSL -o /tmp/${LONG}.py 'https://uploads.example.com/${LONG}.py'`}
    />
  ),
};
