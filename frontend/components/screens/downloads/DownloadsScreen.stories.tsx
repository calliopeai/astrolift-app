import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  DETECTED_PLATFORM,
  DETECTED_PLATFORM_LONG,
} from "@/components/screens/events/events-downloads.fixtures";

import { DownloadsScreen } from "./DownloadsScreen";

const meta: Meta = {
  title: "Screens/Downloads/DownloadsScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

/** The visitor's platform was detected: it is featured, the rest follow. */
export const Full: Story = {
  render: () => <DownloadsScreen detected={DETECTED_PLATFORM} detectionDone />,
};

/** Detection still running (SSR and first paint). */
export const Loading: Story = {
  render: () => <DownloadsScreen detected={null} detectionDone={false} />,
};

/**
 * Detection was inconclusive. The page has no error state (nothing is
 * fetched); this fallback is the closest.
 */
export const Empty: Story = {
  render: () => <DownloadsScreen detected={null} detectionDone />,
};

export const LongStrings: Story = {
  render: () => <DownloadsScreen detected={DETECTED_PLATFORM_LONG} detectionDone />,
};
