import type { StorybookConfig } from "@storybook/nextjs-vite";

/**
 * The design-system catalog (spec 44 §8): every primitive and archetype in
 * every state. Built in CI, so a story that stops rendering fails the build.
 */
const config: StorybookConfig = {
  framework: "@storybook/nextjs-vite",
  stories: ["../components/**/*.stories.@(ts|tsx)"],
  addons: ["@storybook/addon-a11y"],
  staticDirs: ["../public"],
  core: { disableTelemetry: true },
};

export default config;
