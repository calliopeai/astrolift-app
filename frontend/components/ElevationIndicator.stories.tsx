import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { ElevationIndicator } from "@/components/ElevationIndicator";

const meta: Meta = { title: "Shell/ElevationIndicator" };
export default meta;

export const Elevated: StoryObj = {
  render: () => (
    <ElevationIndicator
      elevated
      secondsRemaining={840}
      deelevating={false}
      onDeelevate={() => {}}
    />
  ),
};
export const Deelevating: StoryObj = {
  render: () => (
    <ElevationIndicator elevated secondsRemaining={60} deelevating onDeelevate={() => {}} />
  ),
};
