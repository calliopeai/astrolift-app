/// <reference types="vite/client" />
import { composeStories, setProjectAnnotations } from "@storybook/react";

type ComposedStory = { run: (context?: { canvasElement?: HTMLElement }) => Promise<void> };
import { describe, expect, it } from "vitest";

import * as preview from "../.storybook/preview";

/**
 * Every story renders (spec 44 §8). The Storybook build only proves stories
 * compile; this renders each one, through the same decorator the catalog
 * uses, so a primitive that throws in any of its states fails the suite.
 */
setProjectAnnotations(preview);

const modules = import.meta.glob<Record<string, unknown>>("./**/*.stories.tsx", {
  eager: true,
});

describe.each(Object.entries(modules))("%s", (_file, module) => {
  const stories = Object.entries(
    composeStories(module as Parameters<typeof composeStories>[0])
  ) as [string, ComposedStory][];
  it.each(stories)("%s renders", async (_name, Story) => {
    // run() mounts the story into a canvas of its own and runs its play
    // function, if it has one.
    const canvasElement = document.createElement("div");
    document.body.appendChild(canvasElement);
    await Story.run({ canvasElement });
    expect(canvasElement.childElementCount).toBeGreaterThan(0);
    canvasElement.remove();
  });
});
