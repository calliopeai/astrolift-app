/// <reference types="vite/client" />
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
    "./TeamsScreen.stories.tsx",
    "./CreateTeamSheet.stories.tsx",
    "./EditTeamSheet.stories.tsx",
    "../projects/SlugStatusHint.stories.tsx",
  ],
  { eager: true }
);
describe.each(Object.entries(modules))("Team presentation story %s", (_file, module) => {
  const stories = Object.entries(
    composeStories(module as Parameters<typeof composeStories>[0])
  ) as [string, { run: (context: { canvasElement: HTMLElement }) => Promise<void> }][];
  it.each(stories)("%s renders", async (_name, Story) => {
    const canvasElement = document.createElement("div");
    document.body.appendChild(canvasElement);
    try {
      await Story.run({ canvasElement });
      expect(canvasElement.childElementCount).toBeGreaterThan(0);
    } finally {
      canvasElement.remove();
    }
  });
});
