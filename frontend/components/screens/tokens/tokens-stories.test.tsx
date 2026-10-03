/// <reference types="vite/client" />
import { runStory } from "@/test/run-story";
import { composeStories, setProjectAnnotations } from "@storybook/react";
import { describe, expect, it, vi } from "vitest";

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
import * as preview from "../../../.storybook/preview";
setProjectAnnotations(preview);
const modules = import.meta.glob<Record<string, unknown>>(
  [
    "./TokensScreen.stories.tsx",
    "./TokenDetailScreen.stories.tsx",
    "./ScopePicker.stories.tsx",
    "../../detail/EntityDetailShell.stories.tsx",
  ],
  { eager: true }
);
describe.each(Object.entries(modules))("API key presentation story %s", (_file, module) => {
  const stories = Object.entries(
    composeStories(module as Parameters<typeof composeStories>[0])
  ) as [string, { run: (context: { canvasElement: HTMLElement }) => Promise<void> }][];
  it.each(stories)("%s renders", async (_name, Story) => {
    const canvasElement = document.createElement("div");
    document.body.appendChild(canvasElement);
    try {
      await runStory(Story, canvasElement);
      expect(canvasElement.childElementCount).toBeGreaterThan(0);
    } finally {
      canvasElement.remove();
    }
  });
});
