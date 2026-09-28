/// <reference types="vite/client" />
import { composeStories, setProjectAnnotations } from "@storybook/react";

type ComposedStory = { run: (context?: { canvasElement?: HTMLElement }) => Promise<void> };
import { describe, expect, it, vi } from "vitest";

// Storybook's Next framework mounts a mock app router; this run has none, so
// stand one in the same way for components that navigate on click.
vi.mock("next/navigation", async (importOriginal) => ({
  ...(await importOriginal<typeof import("next/navigation")>()),
  useRouter: () => ({
    push: () => {},
    replace: () => {},
    back: () => {},
    forward: () => {},
    refresh: () => {},
    prefetch: () => {},
  }),
}));

import * as preview from "../.storybook/preview";

/**
 * Every story renders (spec 44 §8). The Storybook build only proves stories
 * compile; this renders each one, through the same decorator the catalog
 * uses, so a primitive that throws in any of its states fails the suite.
 */
setProjectAnnotations(preview);

// jsdom has no matchMedia; components that read the viewport (the sidebar's
// mobile check) get a desktop answer, as in a browser at a wide window.
if (!window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }) as MediaQueryList;
}

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
